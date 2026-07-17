\# IntelCrop — Area Catalog v1 Diagnostic



\## 1. Stato



| Campo | Valore |

|---|---|

| Catalog version | `area\_catalog\_v1\_diagnostic` |

| Catalog status | `diagnostic\_not\_final` |

| Model version | `regional\_reliability\_score\_exp\_v3` |

| Model status | `experimental` |

| Geographic scope | Calabria |

| Source pool | `candidate\_pool\_v2\_area\_ge\_0\_5` |

| Training sample | `olive\_visual\_review\_sample\_v2` |

| Training observations | 406 |

| Positive observations | 319 |

| Negative observations | 87 |

| Uncertain excluded | 94 |



Il catalogo è una risorsa diagnostica sperimentale. Non costituisce una

mappatura definitiva o certificata degli oliveti e non deve essere utilizzato

come unica fonte per decisioni amministrative, contributive o sanzionatorie.



\## 2. Obiettivo



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



\## 3. Numerosità corrente



| Classe | Numero aree |

|---|---:|

| Low | 26.766 |

| Compatible | 10.789 |

| High | 1.664 |

| Very high | 1.042 |

| Totale | 40.261 |

| High + very high | 2.706 |



Le aree `high` e `very\_high` costituiscono le candidate prioritarie

diagnostiche.



\## 4. Classi di affidabilità



| Codice | Intervallo | Interpretazione |

|---|---:|---|

| `low` | 0,00–0,50 | Area non prioritaria o da verificare solo se strategica |

| `compatible` | 0,50–0,70 | Area potenzialmente compatibile con oliveto |

| `high` | 0,70–0,85 | Area candidata per approfondimento operativo |

| `very\_high` | 0,85–1,00 | Area candidata prioritaria |



Le soglie sono sperimentali e versionate insieme al modello.



\## 5. Modello v3



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



Il campo `plantation\_pattern\_v2` è mantenuto per audit e controllo visuale ma

non viene usato come predittore automatico.



\### Metriche correnti



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



\## 6. Limitazioni principali



1\. Il campione è stratificato e non rappresenta un campione casuale puro

&#x20;  dell'intero territorio regionale.

2\. Il nord Calabria e le aree `added\_candidate` sono sovracampionate.

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



\## 7. Database



\### View principali



```text

olive\_candidate\_pool\_v2\_reliability\_v3\_diagnostic\_v1

area\_catalog\_v1\_diagnostic

area\_catalog\_v1\_entity\_scope

