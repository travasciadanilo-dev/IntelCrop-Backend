from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any
import ee
import json
import datetime
import traceback

app = FastAPI(title="IntelCrop GEE API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inizializza GEE - MODIFICATO
try:
    ee.Initialize()
    print("[INFO] Google Earth Engine inizializzato correttamente")
except Exception as e:
    print("[ERROR] Errore inizializzazione Google Earth Engine:", str(e))
    raise

class FieldRequest(BaseModel):
    geojson: Dict[str, Any]

@app.get("/")
def root():
    return {"status": "IntelCrop GEE API online"}

@app.get("/test-gee")
def test_gee():
    """
    Endpoint di test per verificare la connettività a Google Earth Engine.
    Restituisce il numero di immagini Sentinel-2 trovate per un punto di test.
    """
    try:
        print("[DEBUG] Endpoint /test-gee chiamato")
        test = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')\
            .filterDate('2026-01-01', '2026-06-01')\
            .filterBounds(ee.Geometry.Point([15.0, 41.0]))\
            .size().getInfo()
        return {
            "status": "ok", 
            "images_found": test,
            "message": f"Connessione GEE OK. Trovate {test} immagini Sentinel-2 per il punto di test (15.0, 41.0)"
        }
    except Exception as e:
        error_detail = traceback.format_exc()
        print("ERRORE COMPLETO test-gee:", error_detail)
        return {
            "status": "error", 
            "detail": str(e),
            "traceback": error_detail,
            "message": "Errore di connessione a Google Earth Engine. Verifica autenticazione e progetto."
        }

@app.post("/analyze")
def analyze_field(req: FieldRequest):
    try:
        geojson = req.geojson
        
        # Estrai la geometria dal GeoJSON (supporta FeatureCollection)
        # ee.FeatureCollection(geojson).geometry() unisce tutte le geometrie
        fieldGeom = ee.FeatureCollection(geojson).geometry()
        
        scale = 10
        cloudThreshold = 0.40
        validPixelThreshold = 10

        endDate = ee.Date(datetime.datetime.now().strftime('%Y-%m-%d'))
        startDate = ee.Date.fromYMD(
            ee.Number(endDate.get('year')), 1, 1
        )

        print(f"[INFO] Analisi avviata per area GeoJSON")
        print(f"[INFO] Periodo: {startDate.getInfo()} - {endDate.getInfo()}")

        # Sentinel-2 + Cloud Score+
        s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
              .filterBounds(fieldGeom)
              .filterDate(startDate, endDate)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 80)))

        csPlus = ee.ImageCollection('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED')

        s2Clear = (s2
            .linkCollection(csPlus, ['cs_cdf'])
            .map(lambda img: img
                .updateMask(img.select('cs_cdf').gte(cloudThreshold))
                .divide(10000)
                .copyProperties(img, ['system:time_start', 'system:index'])
            ))

        def addIndices(img):
            b  = img.select('B2')
            r  = img.select('B4')
            re = img.select('B5')
            n  = img.select('B8')
            s1 = img.select('B11')
            s2b= img.select('B12')
            ndvi = n.subtract(r).divide(n.add(r)).rename('NDVI')
            evi  = (n.subtract(r).multiply(2.5)
                    .divide(n.add(r.multiply(6))
                    .subtract(b.multiply(7.5)).add(1))
                    .rename('EVI'))
            ndmi = n.subtract(s1).divide(n.add(s1)).rename('NDMI')
            ndre = n.subtract(re).divide(n.add(re)).rename('NDRE')
            msi  = s1.divide(n).rename('MSI')
            psri = r.subtract(b).divide(re).rename('PSRI')
            nbr  = n.subtract(s2b).divide(n.add(s2b)).rename('NBR')
            return img.addBands([ndvi,evi,ndmi,ndre,msi,psri,nbr])

        indexed = s2Clear.map(addIndices)

        def addDate(img):
            d = ee.Date(img.get('system:time_start')).format('YYYY-MM-dd')
            return img.set('date_string', d)

        withDate = indexed.map(addDate)
        
        daily = (withDate
            .map(lambda img: img.set(
                'system:time_start',
                ee.Date(img.get('date_string')).millis()
            ))
            .sort('system:time_start'))

        def addValidPercent(img):
            v = (ee.Number(img.select('NDVI').mask()
                .reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=fieldGeom,
                    scale=scale,
                    maxPixels=1e9
                ).get('NDVI'))
                .multiply(100))
            return img.set('valid_percent', v)

        clean = (daily
            .map(addValidPercent)
            .filter(ee.Filter.gte('valid_percent', validPixelThreshold))
            .sort('system:time_start'))

        cleanSize = clean.size().getInfo()
        print(f"[INFO] Immagini trovate dopo filtri: {cleanSize}")
        
        if cleanSize == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Nessuna immagine Sentinel-2 valida trovata per questo campo. "
                       f"Prova a disegnare un campo piu grande (almeno 1-2 ha) o in una zona diversa."
            )

        # Trend stagionale
        trendBands = ['NDVI','EVI','NDMI','NDRE','MSI','PSRI','NBR']

        def makeTrendFeature(img):
            stats = (img.select(trendBands)
                .reduceRegion(
                    reducer=ee.Reducer.median(),
                    geometry=fieldGeom,
                    scale=scale,
                    maxPixels=1e9
                ))
            return (ee.Feature(None, stats)
                .set('date', ee.Date(img.get('system:time_start')).format('YYYY-MM-dd')))

        # CORREZIONE: conversione esplicita a FeatureCollection
        trendFC = ee.FeatureCollection(clean.map(makeTrendFeature))
        
        try:
            trendData = trendFC.select(
                ['date','NDVI','EVI','NDMI','NDRE','MSI','PSRI','NBR']
            ).getInfo()['features']
            trendData = [f['properties'] for f in trendData]
            print(f"[INFO] Trend data estratti: {len(trendData)} record")
        except Exception as e:
            print(f"[WARN] Errore estrazione trend: {str(e)}")
            trendData = []

        # Ultimi 3 per Mahalanobis
        last3 = clean.sort('system:time_start', False).limit(3)
        selectedIndices = ['EVI','NDMI','NDRE','MSI','PSRI']

        def robustMahal(img):
            img = ee.Image(img)
            
            zImagesList = []
            for name in selectedIndices:
                band = img.select(name)
                med = ee.Number(band.reduceRegion(
                    reducer=ee.Reducer.median(),
                    geometry=fieldGeom, scale=scale, maxPixels=1e9
                ).get(name))
                mad = ee.Number(band.subtract(med).abs().reduceRegion(
                    reducer=ee.Reducer.median(),
                    geometry=fieldGeom, scale=scale, maxPixels=1e9
                ).get(name))
                mad = ee.Number(ee.Algorithms.If(mad.eq(0), 0.0001, mad))
                z = (band.subtract(med)
                     .divide(mad.multiply(1.4826))
                     .rename(name + '_z'))
                zImagesList.append(z)

            zImg = ee.Image.cat(zImagesList)
            
            cov = ee.Array(zImg.toArray().reduceRegion(
                reducer=ee.Reducer.centeredCovariance(),
                geometry=fieldGeom, scale=scale, maxPixels=1e9
            ).get('array'))
            invCov = (cov.add(ee.Array.identity(5).multiply(0.01))
                      .matrixInverse())
            x = zImg.toArray().toArray(1)
            mahal = (x.arrayTranspose()
                     .matrixMultiply(ee.Image(invCov))
                     .matrixMultiply(x)
                     .arrayGet([0, 0]).sqrt()
                     .rename('Mahalanobis_Score'))
            return (img.addBands(mahal)
                    .copyProperties(img, ['system:time_start', 'date_string']))

        anomaly = last3.map(robustMahal)

        def classifyAnom(img):
            img = ee.Image(img)
            thr = ee.Number(img.select('Mahalanobis_Score').reduceRegion(
                reducer=ee.Reducer.percentile([90]),
                geometry=fieldGeom, scale=scale, maxPixels=1e9
            ).get('Mahalanobis_Score'))
            return (img
                .addBands(img.select('Mahalanobis_Score').gte(thr).rename('anomaly_mask'))
                .copyProperties(img, ['system:time_start']))

        classified = anomaly.map(classifyAnom)
        persistence = classified.select('anomaly_mask').sum().rename('Persistence')

        currentScore = (ee.Image(anomaly.sort('system:time_start', False).first())
                        .select('Mahalanobis_Score').clip(fieldGeom))

        def getPercentile(p):
            return ee.Number(currentScore.reduceRegion(
                reducer=ee.Reducer.percentile([p]),
                geometry=fieldGeom, scale=scale, maxPixels=1e9
            ).get('Mahalanobis_Score'))

        p70 = getPercentile(70)
        p85 = getPercentile(85)
        p95 = getPercentile(95)

        # ============================================================
        # DIRECTION SCORE - Direzione agronomica della differenza
        # ============================================================

        latestIndexedImage = ee.Image(
            clean.sort('system:time_start', False).first()
        ).clip(fieldGeom)

        def getMedianValue(image, band):
            return ee.Number(image.select(band).reduceRegion(
                reducer=ee.Reducer.median(),
                geometry=fieldGeom,
                scale=scale,
                maxPixels=1e9
            ).get(band))

        medEVI = getMedianValue(latestIndexedImage, 'EVI')
        medNDMI = getMedianValue(latestIndexedImage, 'NDMI')
        medNDRE = getMedianValue(latestIndexedImage, 'NDRE')
        medMSI = getMedianValue(latestIndexedImage, 'MSI')
        medPSRI = getMedianValue(latestIndexedImage, 'PSRI')

        positiveScore = (
            latestIndexedImage.select('EVI').gt(medEVI)
            .add(latestIndexedImage.select('NDMI').gt(medNDMI))
            .add(latestIndexedImage.select('NDRE').gt(medNDRE))
            .add(latestIndexedImage.select('MSI').lt(medMSI))
            .add(latestIndexedImage.select('PSRI').lt(medPSRI))
            .rename('Positive_Response_Score')
        )

        negativeScore = (
            latestIndexedImage.select('EVI').lt(medEVI)
            .add(latestIndexedImage.select('NDMI').lt(medNDMI))
            .add(latestIndexedImage.select('NDRE').lt(medNDRE))
            .add(latestIndexedImage.select('MSI').gt(medMSI))
            .add(latestIndexedImage.select('PSRI').gt(medPSRI))
            .rename('Negative_Response_Score')
        )

        # ============================================================
        # INTELCROP RESPONSE MAP
        # 1 = Ordinary Zone
        # 2 = High Performance Zone
        # 3 = Low-confidence Priority Zone
        # 4 = Medium-confidence Priority Zone
        # 5 = High-confidence Priority Zone
        # ============================================================

        negativeCandidate = (
            currentScore.gte(p70)
            .And(negativeScore.gte(3))
            .And(negativeScore.gt(positiveScore))
        )

        positiveCandidate = (
            currentScore.gte(p70)
            .And(positiveScore.gte(3))
            .And(positiveScore.gt(negativeScore))
        )

        positiveReliable = positiveCandidate.And(persistence.gte(2))

        priority = (ee.Image(1)
            .where(positiveReliable, 2)
            .where(negativeCandidate.And(persistence.eq(1)), 3)
            .where(negativeCandidate.And(persistence.eq(2)), 4)
            .where(negativeCandidate.And(persistence.eq(3)), 5)
            .rename('IntelCrop_Response_Map')
            .clip(fieldGeom))

        # Statistiche direzionali
        directionMask = currentScore.gte(p70).selfMask()

        positiveMean = positiveScore.updateMask(directionMask).reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=fieldGeom,
            scale=scale,
            maxPixels=1e9
        ).get('Positive_Response_Score')

        negativeMean = negativeScore.updateMask(directionMask).reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=fieldGeom,
            scale=scale,
            maxPixels=1e9
        ).get('Negative_Response_Score')

        positiveMeanVal = ee.Number(ee.Algorithms.If(positiveMean, positiveMean, 0)).getInfo()
        negativeMeanVal = ee.Number(ee.Algorithms.If(negativeMean, negativeMean, 0)).getInfo()

        if positiveMeanVal >= 3.5 and positiveMeanVal > negativeMeanVal:
            directionClass = "High Performance Zone"
            directionLabel = "High Performance Zone"
            directionDescription = (
                "Le aree individuate mostrano valori medi superiori "
                "rispetto al comportamento prevalente del campo per "
                "uno o più indicatori di vigore, stato idrico o attività vegetativa."
            )
        elif negativeMeanVal >= 3.5 and negativeMeanVal > positiveMeanVal:
            directionClass = "Low Performance Zone"
            directionLabel = "Low Performance Zone"
            directionDescription = (
                "Le aree individuate mostrano valori medi inferiori "
                "rispetto al comportamento prevalente del campo per "
                "uno o più indicatori di vigore, stato idrico o attività vegetativa."
            )
        else:
            directionClass = "Reference Zone"
            directionLabel = "Reference Zone"
            directionDescription = (
                "Le aree individuate presentano condizioni complessivamente "
                "in linea con il comportamento prevalente del campo."
            )

        # Area per classe
        pixelArea = ee.Image.pixelArea()
        totalArea = ee.Number(pixelArea.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=fieldGeom, scale=scale, maxPixels=1e9
        ).get('area'))

        areaByClass = (pixelArea.addBands(priority).reduceRegion(
            reducer=ee.Reducer.sum().group(groupField=1, groupName='priority_class'),
            geometry=fieldGeom, scale=scale, maxPixels=1e9
        ))

        totalAreaVal = totalArea.getInfo()
        areaGroups = areaByClass.getInfo().get('groups', [])

        CLASS_LABELS = {
            1: 'Zona ordinaria',
            2: 'Zona ad alta risposta',
            3: 'Priorità emergente',
            4: 'Priorità confermata',
            5: 'Priorità persistente',
        }

        CLASS_COLORS = {
            1: '#91cf60',
            2: '#1a9850',
            3: '#fee08b',
            4: '#fc8d59',
            5: '#d73027',
        }

        priorityAreas = []
        for g in sorted(areaGroups, key=lambda x: x['priority_class']):
            cls = int(g['priority_class'])
            area_m2 = g['sum']
            area_ha = round(area_m2 / 10000, 2)
            perc = round(area_m2 / totalAreaVal * 100, 1)
            priorityAreas.append({
                'class': cls,
                'label': CLASS_LABELS.get(cls, str(cls)),
                'color': CLASS_COLORS.get(cls, '#888'),
                'area_ha': area_ha,
                'percent': perc,
            })

        # ============================================================
        # CLASS STATS - Statistiche descrittive per classe Response Map
        # ============================================================

        statsBands = ['EVI', 'NDMI', 'NDRE', 'MSI', 'PSRI']

        def safeRound(value, digits=4):
            try:
                if value is None:
                    return None
                return round(float(value), digits)
            except:
                return None

        classStats = {}

        latestStatsImage = latestIndexedImage.select(statsBands)

        for cls in [1, 2, 3, 4, 5]:
            classMask = priority.eq(cls).selfMask()

            classArea = pixelArea.updateMask(classMask).reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=fieldGeom,
                scale=scale,
                maxPixels=1e9
            ).get('area')

            classAreaVal = ee.Number(
                ee.Algorithms.If(classArea, classArea, 0)
            ).getInfo()

            if classAreaVal == 0:
                classStats[str(cls)] = {
                    "class": cls,
                    "label": CLASS_LABELS.get(cls, str(cls)),
                    "area_ha": 0,
                    "percent": 0,
                    "indices": {}
                }
                continue

            reducer = (
                ee.Reducer.mean()
                .combine(ee.Reducer.median(), sharedInputs=True)
                .combine(ee.Reducer.stdDev(), sharedInputs=True)
                .combine(ee.Reducer.percentile([25, 75]), sharedInputs=True)
            )

            stats = latestStatsImage.updateMask(classMask).reduceRegion(
                reducer=reducer,
                geometry=fieldGeom,
                scale=scale,
                maxPixels=1e9
            ).getInfo()

            classStats[str(cls)] = {
                "class": cls,
                "label": CLASS_LABELS.get(cls, str(cls)),
                "area_ha": round(classAreaVal / 10000, 2),
                "percent": round(classAreaVal / totalAreaVal * 100, 1),
                "indices": {}
            }

            for band in statsBands:
                classStats[str(cls)]["indices"][band] = {
                    "mean": safeRound(stats.get(f"{band}_mean")),
                    "median": safeRound(stats.get(f"{band}_median")),
                    "std": safeRound(stats.get(f"{band}_stdDev")),
                    "p25": safeRound(stats.get(f"{band}_p25")),
                    "p75": safeRound(stats.get(f"{band}_p75")),
                }

        # Delta rispetto alla zona ordinaria
        referenceStats = classStats.get("1", {}).get("indices", {})

        for cls in ["2", "3", "4", "5"]:
            if cls not in classStats:
                continue

            classStats[cls].setdefault("indices", {})

            for band in statsBands:
                classStats[cls]["indices"].setdefault(band, {
                    "mean": None,
                    "median": None,
                    "std": None,
                    "p25": None,
                    "p75": None,
                })

                refMedian = referenceStats.get(band, {}).get("median")
                clsMedian = classStats[cls]["indices"][band].get("median")

                if refMedian is not None and clsMedian is not None:
                    classStats[cls]["indices"][band]["delta_ref"] = safeRound(clsMedian - refMedian)
                else:
                    classStats[cls]["indices"][band]["delta_ref"] = None

        # ============================================================
        # AGRONOMIC CONTEXT - Sintesi strutturata per insight/AI
        # ============================================================

        def getAreaPercent(class_id):
            for item in priorityAreas:
                if item["class"] == class_id:
                    return float(item.get("percent", 0))
            return 0.0

        def getAreaHa(class_id):
            for item in priorityAreas:
                if item["class"] == class_id:
                    return float(item.get("area_ha", 0))
            return 0.0

        ordinaryPct = getAreaPercent(1)
        highPerformancePct = getAreaPercent(2)
        emergingPct = getAreaPercent(3)
        confirmedPct = getAreaPercent(4)
        persistentPct = getAreaPercent(5)

        priorityPct = emergingPct + confirmedPct + persistentPct
        confirmedPriorityPct = confirmedPct + persistentPct

        priorityHa = getAreaHa(3) + getAreaHa(4) + getAreaHa(5)

        if persistentPct >= 5 or priorityPct >= 12:
            agronomicLevel = "elevata"
        elif confirmedPriorityPct >= 5 or priorityPct >= 5:
            agronomicLevel = "moderata"
        elif priorityPct > 0:
            agronomicLevel = "bassa"
        else:
            agronomicLevel = "ordinaria"

        agronomicContext = {
            "ordinary_percent": round(ordinaryPct, 1),
            "high_performance_percent": round(highPerformancePct, 1),
            "priority_percent": round(priorityPct, 1),
            "priority_area_ha": round(priorityHa, 2),
            "emerging_percent": round(emergingPct, 1),
            "confirmed_percent": round(confirmedPct, 1),
            "persistent_percent": round(persistentPct, 1),
            "confirmed_priority_percent": round(confirmedPriorityPct, 1),
            "attention_level": agronomicLevel,
            "vdi_class": None,  # Verrà sovrascritto dopo il calcolo VDI
            "vdi_score": None,  # Verrà sovrascritto dopo il calcolo VDI
        }

        # ============================================================
        # COMPARISON CONTEXT
        # ============================================================

        comparisonContext = {
            "reference_available": ordinaryPct >= 1,
            "high_response_available": highPerformancePct >= 1,
            "priority_available": priorityPct >= 1,
            "priority_percent": round(priorityPct, 1),
            "high_response_percent": round(highPerformancePct, 1),
            "mode": (
                "reference_high_priority"
                if highPerformancePct >= 1 and priorityPct >= 1 else
                "reference_priority"
                if priorityPct >= 1 else
                "reference_high"
                if highPerformancePct >= 1 else
                "insufficient_priority_area"
            ),
            "message": (
                "Le zone prioritarie coprono meno dell'1% della superficie analizzata; il confronto temporale con le zone prioritarie non è sufficientemente robusto."
                if priorityPct < 1 else
                None
            )
        }

        # ============================================================
        # VES / VDI - Maschere coerenti con IntelCrop Response Map
        # ============================================================

        latestDate = ee.Date(
            ee.Image(clean.sort('system:time_start', False).first())
            .get('system:time_start')
        )
        vesStart = latestDate.advance(-60, 'day')
        
        # Maschere coerenti con IntelCrop Response Map
        referenceMask = priority.eq(1).selfMask()
        priorityInspectionMask = priority.gte(3).selfMask()
        highResponseMask = priority.eq(2).selfMask()
        
        # Indici usati dalla Priority Survey Map
        vdiBands = ['EVI', 'NDMI', 'NDRE', 'MSI', 'PSRI']

        vesCollection = clean.filterDate(vesStart, endDate)

        def safeDelta(a, b):
            return ee.Algorithms.If(
                ee.Algorithms.IsEqual(a, None),
                None,
                ee.Algorithms.If(
                    ee.Algorithms.IsEqual(b, None),
                    None,
                    ee.Number(a).subtract(ee.Number(b))
                )
            )

        def makeVesFeature(img):
            img = ee.Image(img)

            referenceStats = img.select(vdiBands).updateMask(referenceMask).reduceRegion(
                reducer=ee.Reducer.median(),
                geometry=fieldGeom,
                scale=scale,
                maxPixels=1e9
            )

            highResponseStats = img.select(vdiBands).updateMask(highResponseMask).reduceRegion(
                reducer=ee.Reducer.median(),
                geometry=fieldGeom,
                scale=scale,
                maxPixels=1e9
            )

            priorityStats = img.select(vdiBands).updateMask(priorityInspectionMask).reduceRegion(
                reducer=ee.Reducer.median(),
                geometry=fieldGeom,
                scale=scale,
                maxPixels=1e9
            )

            dEVI = safeDelta(priorityStats.get('EVI'), referenceStats.get('EVI'))
            dNDMI = safeDelta(priorityStats.get('NDMI'), referenceStats.get('NDMI'))
            dNDRE = safeDelta(priorityStats.get('NDRE'), referenceStats.get('NDRE'))
            dMSI = safeDelta(priorityStats.get('MSI'), referenceStats.get('MSI'))
            dPSRI = safeDelta(priorityStats.get('PSRI'), referenceStats.get('PSRI'))

            dHighEVI = safeDelta(highResponseStats.get('EVI'), referenceStats.get('EVI'))
            dHighNDMI = safeDelta(highResponseStats.get('NDMI'), referenceStats.get('NDMI'))
            dHighNDRE = safeDelta(highResponseStats.get('NDRE'), referenceStats.get('NDRE'))
            dHighMSI = safeDelta(highResponseStats.get('MSI'), referenceStats.get('MSI'))
            dHighPSRI = safeDelta(highResponseStats.get('PSRI'), referenceStats.get('PSRI'))

            return (ee.Feature(None)
                .set('date', ee.Date(img.get('system:time_start')).format('YYYY-MM-dd'))
                .set('system:time_start', img.get('system:time_start'))

                # Zone prioritarie negative (classi 3, 4, 5)
                .set('priority_EVI', priorityStats.get('EVI'))
                .set('priority_NDMI', priorityStats.get('NDMI'))
                .set('priority_NDRE', priorityStats.get('NDRE'))
                .set('priority_MSI', priorityStats.get('MSI'))
                .set('priority_PSRI', priorityStats.get('PSRI'))

                # Zone ad alta risposta (classe 2)
                .set('high_EVI', highResponseStats.get('EVI'))
                .set('high_NDMI', highResponseStats.get('NDMI'))
                .set('high_NDRE', highResponseStats.get('NDRE'))
                .set('high_MSI', highResponseStats.get('MSI'))
                .set('high_PSRI', highResponseStats.get('PSRI'))

                # Zone ordinarie (classe 1)
                .set('reference_EVI', referenceStats.get('EVI'))
                .set('reference_NDMI', referenceStats.get('NDMI'))
                .set('reference_NDRE', referenceStats.get('NDRE'))
                .set('reference_MSI', referenceStats.get('MSI'))
                .set('reference_PSRI', referenceStats.get('PSRI'))

                # Delta: zone prioritarie negative - zone ordinarie
                .set('delta_EVI', dEVI)
                .set('delta_NDMI', dNDMI)
                .set('delta_NDRE', dNDRE)
                .set('delta_MSI', dMSI)
                .set('delta_PSRI', dPSRI)

                # Delta: zone ad alta risposta - zone ordinarie
                .set('delta_high_EVI', dHighEVI)
                .set('delta_high_NDMI', dHighNDMI)
                .set('delta_high_NDRE', dHighNDRE)
                .set('delta_high_MSI', dHighMSI)
                .set('delta_high_PSRI', dHighPSRI)
            )

        # CORREZIONE: conversione esplicita a FeatureCollection
        vesFC = ee.FeatureCollection(vesCollection.map(makeVesFeature))
        
        try:
            vesData = vesFC.select([
                'date',

                'priority_EVI',
                'priority_NDMI',
                'priority_NDRE',
                'priority_MSI',
                'priority_PSRI',

                'high_EVI',
                'high_NDMI',
                'high_NDRE',
                'high_MSI',
                'high_PSRI',

                'reference_EVI',
                'reference_NDMI',
                'reference_NDRE',
                'reference_MSI',
                'reference_PSRI',

                'delta_EVI',
                'delta_NDMI',
                'delta_NDRE',
                'delta_MSI',
                'delta_PSRI',

                'delta_high_EVI',
                'delta_high_NDMI',
                'delta_high_NDRE',
                'delta_high_MSI',
                'delta_high_PSRI'
            ]).getInfo()['features']
            vesData = [f['properties'] for f in vesData]
            print(f"[INFO] VDI data estratti: {len(vesData)} record")
            
            # Crea vdiTimeSeries
            vdiTimeSeries = []
            for r in vesData:
                if r.get('delta_NDMI') is not None:
                    vdiTimeSeries.append({
                        'date': r.get('date'),
                        'vdi_proxy': round(float(r.get('delta_NDMI')), 6),
                        'delta_NDMI': round(float(r.get('delta_NDMI')), 6),
                        'delta_EVI': round(float(r.get('delta_EVI')), 6) if r.get('delta_EVI') is not None else None,
                        'delta_NDRE': round(float(r.get('delta_NDRE')), 6) if r.get('delta_NDRE') is not None else None,
                        'delta_MSI': round(float(r.get('delta_MSI')), 6) if r.get('delta_MSI') is not None else None,
                        'delta_PSRI': round(float(r.get('delta_PSRI')), 6) if r.get('delta_PSRI') is not None else None,
                    })
        except Exception as e:
            print(f"[WARN] Errore estrazione VDI: {str(e)}")
            vesData = []
            vdiTimeSeries = []

        # ============================================================
        # VDI - Vegetation Divergence Index (protetto se priorityPct < 1%)
        # ============================================================

        if priorityPct < 1:
            vdiScore = None
            vdiClass = "Insufficient priority area"
        else:
            validVdi = [r for r in vesData if r.get('delta_NDMI') is not None]

            if len(validVdi) >= 2:
                n = len(validVdi)
                xs = list(range(n))
                ys = [float(r['delta_NDMI']) for r in validVdi]

                sumX = sum(xs)
                sumY = sum(ys)
                sumXY = sum(x * y for x, y in zip(xs, ys))
                sumX2 = sum(x * x for x in xs)

                denom = n * sumX2 - sumX * sumX

                if denom != 0:
                    vdiScore = (n * sumXY - sumX * sumY) / denom
                else:
                    vdiScore = None

                if vdiScore is None:
                    vdiClass = "Insufficient priority area"
                else:
                    vdiClass = (
                        'Recovery'             if vdiScore < -0.001 else
                        'Stable'               if vdiScore < 0.001  else
                        'Slight Divergence'    if vdiScore < 0.003  else
                        'Moderate Divergence'  if vdiScore < 0.006  else
                        'Strong Divergence'
                    )

            else:
                vdiScore = None
                vdiClass = "Insufficient priority area"

        # Aggiorna agronomicContext con i valori VDI calcolati
        agronomicContext["vdi_class"] = vdiClass
        agronomicContext["vdi_score"] = round(vdiScore, 6) if vdiScore is not None else None

        # CORREZIONE: estrazione sicura dell'ultima data
        lastDateStr = ''
        if trendData:
            dates = [r.get('date', '') for r in trendData if r.get('date')]
            if dates:
                lastDateStr = sorted(dates)[-1]

        # ============================================================
        # MAP LAYERS - Earth Engine tile URLs
        # ============================================================
        try:
            # Funzione per ottenere parametri di visualizzazione con percentili
            def getVisParams(image, band, palette):
                stats = image.select(band).reduceRegion(
                    reducer=ee.Reducer.percentile([5, 95]),
                    geometry=fieldGeom,
                    scale=scale,
                    maxPixels=1e9
                )

                p5 = ee.Number(stats.get(f'{band}_p5'))
                p95 = ee.Number(stats.get(f'{band}_p95'))

                return {
                    'min': p5.getInfo(),
                    'max': p95.getInfo(),
                    'palette': palette
                }

            # Ottieni l'ultima immagine pulita per gli altri indici
            latestImage = ee.Image(clean.sort('system:time_start', False).first()).clip(fieldGeom)

            # STEP 1: Palette separate per ogni indice
            eviPalette = ['8b0000', 'ff4500', 'ffd700', '7fff00', '006400']
            ndmiPalette = ['8b4513', 'd2b48c', 'ffffcc', '7fcdbb', '2c7fb8', '253494']
            ndrePalette = ['7f0000', 'd7301f', 'fc8d59', 'fee08b', '91cf60', '1a9850']
            ndviPalette = ['a50026', 'd73027', 'f46d43', 'fee08b', '66bd63', '1a9850', '006837']

            # Parametri di visualizzazione dinamici
            eviVis = getVisParams(latestImage, 'EVI', eviPalette)
            ndmiVis = getVisParams(latestImage, 'NDMI', ndmiPalette)
            ndreVis = getVisParams(latestImage, 'NDRE', ndrePalette)
            ndviVis = getVisParams(latestImage, 'NDVI', ndviPalette)

            priorityMapId = priority.getMapId({
                'min': 1,
                'max': 5,
                'palette': ['91cf60', '1a9850', 'fee08b', 'fc8d59', 'd73027']
            })

            eviMapId = latestImage.select('EVI').getMapId(eviVis)
            ndmiMapId = latestImage.select('NDMI').getMapId(ndmiVis)
            ndreMapId = latestImage.select('NDRE').getMapId(ndreVis)
            ndviMapId = latestImage.select('NDVI').getMapId(ndviVis)

            # STEP 2: MapLayers con palette e legendLabels - OPACITÀ AGGIORNATE
            mapLayers = {
                "priority": {
                    "name": "IntelCrop Response Map",
                    "type": "ee_tile",
                    "url": priorityMapId["tile_fetcher"].url_format,
                    "opacity": 0.75,
                    "legend": [
                        {"class": 1, "label": "Zona ordinaria", "color": "#91cf60"},
                        {"class": 2, "label": "Zona ad alta risposta", "color": "#1a9850"},
                        {"class": 3, "label": "Priorità emergente", "color": "#fee08b"},
                        {"class": 4, "label": "Priorità confermata", "color": "#fc8d59"},
                        {"class": 5, "label": "Priorità persistente", "color": "#d73027"},
                    ]
                },
                "evi": {
                    "name": "EVI",
                    "type": "ee_tile",
                    "url": eviMapId["tile_fetcher"].url_format,
                    "opacity": 0.70,
                    "group": "Vegetation vigor",
                    "min": eviVis["min"],
                    "max": eviVis["max"],
                    "palette": eviPalette,
                    "legendLabels": {
                        "low": "Low vigor",
                        "high": "High vigor"
                    }
                },
                "ndmi": {
                    "name": "NDMI",
                    "type": "ee_tile",
                    "url": ndmiMapId["tile_fetcher"].url_format,
                    "opacity": 0.70,
                    "group": "Water status",
                    "min": ndmiVis["min"],
                    "max": ndmiVis["max"],
                    "palette": ndmiPalette,
                    "legendLabels": {
                        "low": "Dry vegetation",
                        "high": "Moist vegetation"
                    }
                },
                "ndre": {
                    "name": "NDRE",
                    "type": "ee_tile",
                    "url": ndreMapId["tile_fetcher"].url_format,
                    "opacity": 0.70,
                    "group": "Early stress / chlorophyll",
                    "min": ndreVis["min"],
                    "max": ndreVis["max"],
                    "palette": ndrePalette,
                    "legendLabels": {
                        "low": "Low chlorophyll",
                        "high": "High chlorophyll"
                    }
                },
                "ndvi": {
                    "name": "NDVI",
                    "type": "ee_tile",
                    "url": ndviMapId["tile_fetcher"].url_format,
                    "opacity": 0.70,
                    "group": "Standard vegetation index",
                    "min": ndviVis["min"],
                    "max": ndviVis["max"],
                    "palette": ndviPalette,
                    "legendLabels": {
                        "low": "Low vegetation",
                        "high": "High vegetation"
                    }
                }
            }

            print("[INFO] Map layers generati: IntelCrop Response Map, EVI, NDMI, NDRE, NDVI")
        except Exception as e:
            print(f"[WARN] Errore generazione map layer: {str(e)}")
            mapLayers = {}

        # DEBUG PRINT
        print("=" * 60)
        print("[DEBUG] RIEPILOGO ANALISI COMPLETATA")
        print("=" * 60)
        print(f"[DEBUG] trendData length: {len(trendData)}")
        print(f"[DEBUG] vdiData length: {len(vesData)}")
        print(f"[DEBUG] vdiTimeSeries length: {len(vdiTimeSeries)}")
        print(f"[DEBUG] lastDateStr: {lastDateStr}")
        print(f"[DEBUG] totalArea: {round(totalAreaVal / 10000, 2)} ha")
        print(f"[DEBUG] priorityAreas count: {len(priorityAreas)}")
        print(f"[DEBUG] classStats keys: {list(classStats.keys())}")
        print(f"[DEBUG] agronomicContext: {agronomicContext}")
        print(f"[DEBUG] comparisonContext: {comparisonContext}")
        print(f"[DEBUG] vdiScore: {round(vdiScore, 6) if vdiScore is not None else 'None'}")
        print(f"[DEBUG] vdiClass: {vdiClass}")
        print(f"[DEBUG] directionClass: {directionClass}")
        print(f"[DEBUG] positive_score: {round(float(positiveMeanVal), 2)}")
        print(f"[DEBUG] negative_score: {round(float(negativeMeanVal), 2)}")
        print(f"[DEBUG] trendData sample: {trendData[:2] if trendData else 'VUOTO'}")
        if vesData:
            print(f"[DEBUG] vdiData first record: {vesData[0] if vesData else 'N/A'}")
        print("=" * 60)

        print("[INFO] Analisi completata con successo")
        
        # JSON finale con compatibilità
        return {
            "status": "ok",
            "lastImageDate": lastDateStr,
            "totalArea": round(totalAreaVal / 10000, 2),
            "priorityAreas": priorityAreas,
            "classStats": classStats,
            "agronomicContext": agronomicContext,
            "comparisonContext": comparisonContext,
            "directionSummary": {
                "class": directionClass,
                "label": directionLabel,
                "description": directionDescription,
                "positive_score": round(float(positiveMeanVal), 2),
                "negative_score": round(float(negativeMeanVal), 2),
            },
            "vdi": {
                "score": round(vdiScore, 6) if vdiScore is not None else None,
                "class": vdiClass,
                "window_days": 60,
            },
            "ves": {
                "score": round(vdiScore, 6) if vdiScore is not None else None,
                "class": vdiClass,
                "window_days": 60,
            },
            "trendData": trendData,
            "vdiData": vesData,
            "vesData": vesData,
            "vdiTimeSeries": vdiTimeSeries,
            "mapLayers": mapLayers,
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print("=" * 60)
        print("ERRORE COMPLETO nell'endpoint /analyze:")
        print(error_detail)
        print("=" * 60)
        raise HTTPException(status_code=500, detail=str(e))