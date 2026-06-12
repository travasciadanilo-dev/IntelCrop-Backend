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

        priority = (ee.Image(1)
            .where(currentScore.gte(p70), 2)
            .where(currentScore.gte(p85), 3)
            .where(persistence.gte(2), 4)
            .where(persistence.gte(2).And(currentScore.gte(p95)), 5)
            .rename('Priority_Survey_Map').clip(fieldGeom))

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
            1: 'Normal condition',
            2: 'Moderate anomaly',
            3: 'Strong anomaly',
            4: 'Persistent anomaly',
            5: 'High inspection priority',
        }
        CLASS_COLORS = {
            1: '#1a9850', 2: '#91cf60', 3: '#fee08b',
            4: '#fc8d59', 5: '#d73027',
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

        # VES / VDI
        latestDate = ee.Date(
            ee.Image(clean.sort('system:time_start', False).first())
            .get('system:time_start')
        )
        vesStart = latestDate.advance(-60, 'day')
        dynamicMask = classified.select('anomaly_mask').sum().gte(1).selfMask()
        healthyMask = priority.lte(3).selfMask()
        vesBands = ['NDMI','EVI','NDRE','MSI']

        vesCollection = clean.filterDate(vesStart, endDate)

        def makeVesFeature(img):
            img = ee.Image(img)
            sanom = img.select(vesBands).updateMask(dynamicMask).reduceRegion(
                reducer=ee.Reducer.median(),
                geometry=fieldGeom, scale=scale, maxPixels=1e9
            )
            sfield = img.select(vesBands).updateMask(healthyMask).reduceRegion(
                reducer=ee.Reducer.median(),
                geometry=fieldGeom, scale=scale, maxPixels=1e9
            )
            dNDMI = ee.Number(sanom.get('NDMI')).subtract(ee.Number(sfield.get('NDMI')))
            dEVI  = ee.Number(sanom.get('EVI')).subtract(ee.Number(sfield.get('EVI')))
            dNDRE = ee.Number(sanom.get('NDRE')).subtract(ee.Number(sfield.get('NDRE')))
            dMSI  = ee.Number(sanom.get('MSI')).subtract(ee.Number(sfield.get('MSI')))
            return (ee.Feature(None)
                .set('date', ee.Date(img.get('system:time_start')).format('YYYY-MM-dd'))
                .set('system:time_start', img.get('system:time_start'))
                .set('delta_NDMI', dNDMI)
                .set('delta_EVI',  dEVI)
                .set('delta_NDRE', dNDRE)
                .set('delta_MSI',  dMSI))

        # CORREZIONE: conversione esplicita a FeatureCollection
        vesFC = ee.FeatureCollection(vesCollection.map(makeVesFeature))
        
        try:
            vesData = vesFC.select(
                ['date','delta_NDMI','delta_EVI','delta_NDRE','delta_MSI']
            ).getInfo()['features']
            vesData = [f['properties'] for f in vesData]
            print(f"[INFO] VDI data estratti: {len(vesData)} record")
        except Exception as e:
            print(f"[WARN] Errore estrazione VDI: {str(e)}")
            vesData = []

        # VDI - Vegetation Divergence Index
        vdiScore = 0.0
        validVdi = [r for r in vesData if r.get('delta_NDMI') is not None]

        if len(validVdi) >= 2:
            n = len(validVdi)
            xs = list(range(n))
            ys = [float(r['delta_NDMI']) for r in validVdi]
            sumX  = sum(xs)
            sumY  = sum(ys)
            sumXY = sum(x*y for x,y in zip(xs,ys))
            sumX2 = sum(x*x for x in xs)
            denom = n * sumX2 - sumX * sumX
            if denom != 0:
                vdiScore = (n * sumXY - sumX * sumY) / denom

        vdiClass = (
            'Recovery'             if vdiScore < -0.001 else
            'Stable'               if vdiScore < 0.001  else
            'Slight Divergence'    if vdiScore < 0.003  else
            'Moderate Divergence'  if vdiScore < 0.006  else
            'Strong Divergence'
        )

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
                'palette': ['1a9850', '91cf60', 'fee08b', 'fc8d59', 'd73027']
            })

            eviMapId = latestImage.select('EVI').getMapId(eviVis)
            ndmiMapId = latestImage.select('NDMI').getMapId(ndmiVis)
            ndreMapId = latestImage.select('NDRE').getMapId(ndreVis)
            ndviMapId = latestImage.select('NDVI').getMapId(ndviVis)

            # STEP 2: MapLayers con palette e legendLabels - OPACITÀ AGGIORNATE
            mapLayers = {
                "priority": {
                    "name": "Priority Survey Map",
                    "type": "ee_tile",
                    "url": priorityMapId["tile_fetcher"].url_format,
                    "opacity": 0.75,  # MODIFICATO: da 0.65 a 0.75
                    "legend": [
                        {"class": 1, "label": "Normal condition", "color": "#1a9850"},
                        {"class": 2, "label": "Moderate anomaly", "color": "#91cf60"},
                        {"class": 3, "label": "Strong anomaly", "color": "#fee08b"},
                        {"class": 4, "label": "Persistent anomaly", "color": "#fc8d59"},
                        {"class": 5, "label": "High inspection priority", "color": "#d73027"},
                    ]
                },
                "evi": {
                    "name": "EVI",
                    "type": "ee_tile",
                    "url": eviMapId["tile_fetcher"].url_format,
                    "opacity": 0.70,  # MODIFICATO: da 0.42 a 0.70
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
                    "opacity": 0.70,  # MODIFICATO: da 0.42 a 0.70
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
                    "opacity": 0.70,  # MODIFICATO: da 0.42 a 0.70
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
                    "opacity": 0.70,  # MODIFICATO: da 0.42 a 0.70
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

            print("[INFO] Map layers generati: Priority, EVI, NDMI, NDRE, NDVI")
        except Exception as e:
            print(f"[WARN] Errore generazione map layer: {str(e)}")
            mapLayers = {}

        # DEBUG PRINT
        print("=" * 60)
        print("[DEBUG] RIEPILOGO ANALISI COMPLETATA")
        print("=" * 60)
        print(f"[DEBUG] trendData length: {len(trendData)}")
        print(f"[DEBUG] vdiData length: {len(vesData)}")
        print(f"[DEBUG] lastDateStr: {lastDateStr}")
        print(f"[DEBUG] totalArea: {round(totalAreaVal / 10000, 2)} ha")
        print(f"[DEBUG] priorityAreas count: {len(priorityAreas)}")
        print(f"[DEBUG] vdiScore: {round(vdiScore, 6)}")
        print(f"[DEBUG] vdiClass: {vdiClass}")
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
            "vdi": {
                "score": round(vdiScore, 6),
                "class": vdiClass,
                "window_days": 60,
            },
            "ves": {
                "score": round(vdiScore, 6),
                "class": vdiClass,
                "window_days": 60,
            },
            "trendData": trendData,
            "vdiData": vesData,
            "vesData": vesData,
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