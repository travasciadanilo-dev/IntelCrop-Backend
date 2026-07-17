# IntelCrop — Area Catalog v1 Diagnostic



## 1. Stato



| Campo | Valore |

|---|---|

| Catalog version | `area_catalog_v1_diagnostic` |

| Catalog status | `diagnostic_not_final` |

| Model version | `regional_reliability_score_exp_v3` |

| Model status | `experimental` |

| Geographic scope | Calabria |

| Source pool | `candidate_pool_v2_area_ge_0_5` |

| Training sample | `olive_visual_review_sample_v2` |

| Training observations | 406 |

| Positive observations | 319 |

| Negative observations | 87 |

| Uncertain excluded | 94 |



Il catalogo è una risorsa diagnostica sperimentale. Non costituisce una

mappatura definitiva o certificata degli oliveti e non deve essere utilizzato

come unica fonte per decisioni amministrative, contributive o sanzionatorie.



## 2. Obiettivo



Il catalogo consente di:



\- individuare aree territoriali potenzialmente compatibili con oliveti;

\- ordinare le aree per affidabilità operativa;

\- selezionare aree prioritarie per analisi successive;

\- supportare verifiche tecniche, satellitari e di campo;

\- ridurre progressivamente il controllo manuale;

\- offrire a PA e consorzi un catalogo filtrato sul territorio di competenza.



Il catalogo non sostituisce:



\- fotointerpretazione esperta;

\- rilievi di campo;

\- dati catastali;

\- fascicoli aziendali;

\- confini ufficiali delle aziende agricole;

\- validazioni amministrative.



## 3. Numerosità corrente



| Classe | Numero aree |

|---|---:|

| Low | 26.766 |

| Compatible | 10.789 |

| High | 1.664 |

| Very high | 1.042 |

| Totale | 40.261 |

| High + very high | 2.706 |



Le aree `high` e `very_high` costituiscono le candidate prioritarie

diagnostiche.



## 4. Classi di affidabilità



| Codice | Intervallo | Interpretazione |

|---|---:|---|

| `low` | 0,00–0,50 | Area non prioritaria o da verificare solo se strategica |

| `compatible` | 0,50–0,70 | Area potenzialmente compatibile con oliveto |

| `high` | 0,70–0,85 | Area candidata per approfondimento operativo |

| `very_high` | 0,85–1,00 | Area candidata prioritaria |



Le soglie sono sperimentali e versionate insieme al modello.



## 5. Modello v3



Il modello è una regressione logistica penalizzata con bilanciamento delle

classi.



Le feature comprendono:



\- area del poligono;

\- perimetro;

\- compattezza;

\- numero di vertici;

\- numero di parti;

\- indicatori geometrici;

\- zona geografica;

\- origine della candidata;

\- corrispondenza con riferimenti precedenti.



Il campo `plantation_pattern_v2` è mantenuto per audit e controllo visuale ma

non viene usato come predittore automatico.



### Metriche correnti



| Metrica | Valore |

|---|---:|

| Precision | 0,848 |

| Recall | 0,592 |

| Specificity | 0,609 |

| F1 | 0,697 |

| Accuracy | 0,596 |

| ROC AUC | 0,631 |

| Brier score | 0,232 |

| Calibration slope | 0,694 |

| Calibration intercept | 1,245 |



Le metriche confermano che il modello è utile per prioritizzazione e

screening, ma non ancora sufficiente per una validazione definitiva.



## 6. Limitazioni principali



1\. Il campione è stratificato e non rappresenta un campione casuale puro

&#x20;  dell'intero territorio regionale.

2\. Il nord Calabria e le aree `added_candidate` sono sovracampionate.

3\. Le probabilità devono essere interpretate come score operativo, non come

&#x20;  probabilità assoluta di presenza di oliveto.

4\. Il modello dipende prevalentemente da feature geometriche e di provenienza.

5\. Feature satellitari temporali e spettrali non sono ancora integrate nel

&#x20;  modello v3.

6\. Le aree `uncertain` sono escluse dal training.

7\. I territori degli enti devono essere sostituiti con confini ufficiali

&#x20;  versionati prima dell'uso in produzione.

8\. Le aree molto estese con score prossimo a 1 devono essere sottoposte a

&#x20;  verifica specifica per evitare effetti dovuti alla dimensione e alla

&#x20;  complessità geometrica.



## 7. Database



### View principali



```text

olive_candidate_pool_v2_reliability_v3_diagnostic_v1

area_catalog_v1_diagnostic

area_catalog_v1_entity_scope

## Catalogo regionale v4.1

### Stato

Il catalogo regionale v4.1 è una versione derivata e validata, ma non ancora promossa come catalogo operativo predefinito.

Stato pubblico: `validated_not_promoted`.

Il backend continua a utilizzare il catalogo v3 salvo configurazione esplicita tramite `AREA_CATALOG_VERSION=v4_1`.

### Classificazione

Schema pubblico:

- `low`: `0.00 <= score < 0.61`
- `compatible`: `0.61 <= score < 0.82`
- `very_high`: `0.82 <= score <= 1.00`

La classe storica `high` resta nei metadati originari del modello v4 per tracciabilità, ma non viene esposta come classe autonoma dall'API v4.1.

### Validazione visuale

Versione: `regional_reliability_v4_1_visual_validation_20260717`.

Risultati principali:

- catalogo regionale: 40.261 aree;
- campione visuale: 240 aree;
- record valutabili: 184;
- record non valutabili: 56;
- quota valutabile pesata: 80,01%;
- compatibilità pesata tra i valutabili: 61,02%;
- intervallo di confidenza 95%: 53,30–68,59%.

Le classi originali `compatible` e `high` mostravano tassi positivi pesati del 60,31% e del 61,70%. La differenza era di 1,39 punti percentuali; il test esatto di Fisher non ha supportato la separazione (`odds ratio = 0.8235`, `p = 0.821891`).

### API e job

Con `AREA_CATALOG_VERSION=v4_1` vengono utilizzati:

- `area_catalog_v4_1_diagnostic`;
- `area_catalog_v4_1_entity_scope`;
- `regional_reliability_score_exp_v4_combined_ridge`.

`GET /areas/metadata` espone soltanto `low`, `compatible` e `very_high`.

I job batch vengono creati tramite `POST /jobs/batch`. La geometria non viene fornita dal client: lo snapshot viene recuperato dal catalogo selezionato dal backend.

Il worker è `scripts/process_analysis_jobs_v1.py` e registra in modo coerente catalogo, modello, snapshot e risultato.

### Migrazioni

- `db/init/033_regional_reliability_v4_1_validation_catalog.sql`
- `db/init/034_regional_reliability_v4_1_validation_run.sql`

### Test

- v3: 42 test superati, 1 ignorato;
- v4.1: 37 test superati, 1 ignorato.

Il catalogo v4.1 non deve essere promosso implicitamente. La promozione richiederà una decisione esplicita e una nuova validazione completa.
