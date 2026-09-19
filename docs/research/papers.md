# Papers

## Reference table

External sources and original-source breakdowns. IDs link to the corresponding
breakdown and remain stable when entries are added. Source PDFs stay outside the
repository. The nine accessible full texts were read for this revision on
2026-09-19; P-010 records the remaining full-text access gap.

| ID | Title | Author(s) | Journal / Conference / Issuer | Year | Online resource |
|---|---|---|---|---|---|
| [P-001](#p-001) | Short-Term Forecasting of Anomalous Load Using Rule-Based Triple Seasonal Methods | Siddharth Arora; James W. Taylor | IEEE Transactions on Power Systems 28(3), 3235–3242 | 2013 | [DOI](https://doi.org/10.1109/TPWRS.2013.2252929) · [Author manuscript](https://arxiv.org/pdf/1409.2027) |
| [P-002](#p-002) | Advances in Similar Day Methods for Short-Term Load Forecasting for Power Systems | Monica Borunda; Luis Conde-López; Gerardo Ruiz-Chavarría; Guadalupe Lopez Lopez; Victor M. Alvarado; Edgardo de Jesús Carrera Avendaño | Forecasting 8(2), 32; review | 2026 | [DOI](https://doi.org/10.3390/forecast8020032) · [PDF](https://www.mdpi.com/2571-9394/8/2/32/pdf) |
| [P-003](#p-003) | Electricity Load and Peak Forecasting: Feature Engineering, Probabilistic LightGBM and Temporal Hierarchies | Nicolò Rubattu; Gabriele Maroni; Giorgio Corani | ECML PKDD 2023, AALTD workshop | 2023 | [Workshop manuscript](https://ecml-aaltd.github.io/aaltd2023/papers/Electricity%20Load%20and%20Peak%20Forecasting_%20Feature%20Engineering,%20Probabilistic%20LightGBM%20and%20Temporal%20Hierarchies.pdf) |
| [P-004](#p-004) | Classification of Special Days in Short-Term Load Forecasting: The Spanish Case Study | Miguel López; Carlos Sans; Sergio Valero; Carolina Senabre | Energies 12(7), 1253 | 2019 | [DOI](https://doi.org/10.3390/en12071253) · [PDF](https://www.mdpi.com/1996-1073/12/7/1253/pdf) |
| [P-005](#p-005) | Efficient mid-term forecasting of hourly electricity load using generalized additive models | Monika Zimmermann; Florian Ziel | arXiv:2405.17070v1; preprint | 2024 | [Version record](https://arxiv.org/abs/2405.17070v1) · [PDF](https://arxiv.org/pdf/2405.17070v1) |
| [P-006](#p-006) | 複数の簡易的なデータ参照方法の組み合わせによる翌日電力需要予測の検討 (A Study of Electricity Demand Forecast with Combination of Several Simplified Data Reference Methods) | 森田圭; 真鍋勇介; 加藤丈佳; 舟橋俊久; 鈴置保雄 | エネルギー・資源学会論文誌 / Journal of Japan Society of Energy and Resources 38(3), 1–10 | 2017 | [DOI](https://doi.org/10.24778/jjser.38.3_1) · [PDF](https://www.jstage.jst.go.jp/article/jjser/38/3/38_1/_pdf/-char/en) |
| [P-007](#p-007) | Short-Term Load Forecasting Algorithm Using a Similar Day Selection Method Based on Reinforcement Learning | Rae-Jun Park; Kyung-Bin Song; Bo-Sung Kwon | Energies 13(10), 2640 | 2020 | [DOI](https://doi.org/10.3390/en13102640) · [PDF](https://www.mdpi.com/1996-1073/13/10/2640/pdf) |
| [P-008](#p-008) | 需要予測装置、方法、及びコンピュータ読み取り可能な記憶媒体 (Demand forecasting apparatus, method and computer-readable storage medium) | 伊勢淳治; 藤崎敬介 | Japan Patent Office; patent JP4448226B2, Nippon Steel | 2010 (filed 2000) | [Patent record and full text](https://patents.google.com/patent/JP4448226B2/ja) · [PDF](https://patentimages.storage.googleapis.com/59/0b/72/3ef12ac47185d9/JP4448226B2.pdf) |
| [P-009](#p-009) | 屋外空間における温冷感指標に関する研究 (A Study on Thermal Indices for the Outdoor Environment) | 木内豪 (Tsuyoshi Kiuchi) | 天気 / Tenki 48(9), 661–671 | 2001 | [PDF](https://www.metsoc.jp/tenki/pdf/2001/2001_09_0661.pdf) |
| [P-010](#p-010) | The Discomfort Index | E. C. Thom | Weatherwise 12(2), 57–61 | 1959 | [Publisher record](https://doi.org/10.1080/00431672.1959.9926960); full text unavailable |

## Breakdowns

Each breakdown concerns the source itself. **Appraisal** identifies a limitation
inferred during this review; other limitations are stated by the authors. Page,
section and table references refer to the linked text, with printed page numbers
where available. P-005 uses the 2024 first version throughout.

### P-001

**Core Topic / Objective**

Forecast ordinary and anomalous British electricity load, especially public
holidays, within a single framework using calendar rules.

**Methodology / Data used**

Rule-based triple-seasonal exponential smoothing, SARMA, neural networks and
SVD smoothing select historical special-day patterns. Half-hourly Great Britain
load from 2001–2008 trains the models; rolling forecasts in 2009 cover 18 special
days and horizons of 0.5–24 hours (§§II, V).

**Key Findings**

Rule 3 roughly halved special-day MAPE for the smoothing, SARMA and SVD models
relative to their versions without rules. Averaging rule-based smoothing and
SARMA performed best; ordinary-day accuracy stayed similar (Figs. 9–11).

**Limitations / Gaps**

The authors propose longer histories and annual lags that vary by time of day.
**Appraisal:** one national series and 18 anomalous test days limit generalization;
the univariate models do not test weather inputs (§VI).

### P-002

**Core Topic / Objective**

Review similar-day load forecasting from 2000–2025 and organize how historical
days are selected and combined with forecasting models.

**Methodology / Data used**

Searches cover five scholarly databases and power-system-level studies. The review
classifies 83 works as conventional, intelligent or hybrid and compares their
reported errors, inputs and study locations (§§3.1–3.3).

**Key Findings**

Conventional methods comprise 39%, intelligent methods 14%, and hybrids 47% of
the classified works. Hybrids have the lowest variability in reported MAPE.
The review emphasizes similarity construction alongside predictor choice
(§§3.7, 4.2.2).

**Limitations / Gaps**

The authors identify scarce extreme-event data, changing load patterns,
underrepresented EV/PV/large-load drivers, and few probabilistic approaches.
**Appraisal:** errors from different datasets and evaluation designs do not
establish a controlled ranking of methods (§§4.2.3–4.2.5).

### P-003

**Core Topic / Objective**

Forecast hourly load, daily peak magnitude and timing, and predictive uncertainty
for the BigDEAL Challenge 2022.

**Methodology / Data used**

Clustered permutation feature selection feeds detrended LightGBM, probabilistic
LightGBM-LSS and temporal reconciliation. Qualification forecasts 2007 from
2002–2006 load and four weather stations, using realized future temperatures.
Finals forecast 2018 for three U.S. utilities using 2015–2017 data, six stations
and weather forecasts (§3).

**Key Findings**

DART reduced qualification MAPE from 3.24% to 2.83%. Reconciliation improved
final-round CRPS by 4.49–5.16% and interval score by about 11.3%, but MAPE by
only 0.05–0.75%. The team finished sixth overall (Tables 5–7).

**Limitations / Gaps**

The authors propose testing other datasets and deep models. **Appraisal:** final
results cover January–October 2018 competition rounds; nominal 90% intervals
cover roughly 98–99%, suggesting conservative uncertainty estimates (§4, Table 5).

### P-004

**Core Topic / Objective**

Test whether detailed calendar classes improve Spanish load modeling on
holidays, adjacent days and vacations.

**Methodology / Data used**

Twenty-four hourly log-load regressions use trend, 30 temperature terms from five
stations, month indicators and up to 53 day types. Mainland-Spain load covers
2010–2017. Eight progressively richer classifications undergo leave-one-year-out
testing (§§3.1–3.4, Tables 5–6).

**Key Findings**

The fullest classification lowered special-day median MAPE from 2.43% to 1.84%
and its 95th percentile from 17.61% to 4.56%. Regular-day median MAPE also fell,
from 2.07% to 1.78% (Table 12).

**Limitations / Gaps**

The authors say other systems require adapted categories. **Appraisal:** evidence
covers one country and expert-designed classes. The deliberately simplified
regression isolates calendar effects; it does not compare complete operational
forecasting systems (§§2, 5).

### P-005

**Core Topic / Objective**

Produce fast, interpretable hourly load forecasts from weeks to one year ahead.

**Methodology / Data used**

P-spline generalized additive models combine modeled temperature, ETS level and
seasonal states, calendar effects and autoregressive residual forecasts. Load,
weather and holidays cover 24 European countries, 2015–February 2024. One hundred
sampled rolling experiments use four-year training windows and 52-week horizons,
with eight benchmarks and component ablations (§§2–4).

**Key Findings**

The best GAM variant in each country outperformed the neural-network benchmark.
Temperature effects improved French accuracy by about 20%; detailed holidays
improved German accuracy by about 10%. GAM estimation took 3.2–5.6 seconds in
the France/Germany timing comparison (Tables 4–5, §§5.4–5.5).

**Limitations / Gaps**

ETS states handled abrupt COVID-era shifts poorly. Temperature forecasts mainly
capture seasonality; probabilistic and neural-ensemble extensions remain future
work. **Appraisal:** validation covers national aggregates and sampled forecast
origins (§§5.2, 6).

### P-006

**Core Topic / Objective**

Develop an inexpensive, interpretable day-ahead residential load forecast for
new electricity retailers.

**Methodology / Data used**

Thirty-minute demand from about 700 fuel-cell-equipped homes (400–500 valid
daily) covers August 2012–July 2013, with Tokyo weather and forecasts. Five
date-reference rules and 360 temperature-rule settings are tested and
conditionally combined into Method 7 (§§2.1, 3.1–3.2).

**Key Findings**

Method 7 reduced annual mean daily percentage MAE from Method 6's 7.0% to 6.2%.
Days above 10% MAE fell from 63 to 45. Estimated imbalance costs fell 10–40%
relative to Methods 1–6 (§5.6, Figs. 13–14).

**Limitations / Gaps**

The authors note unusually high-demand homes, post-earthquake conservation
and the need for other years and datasets. **Appraisal:** parameter selection
and evaluation use the same year; no held-out year is described (§§2.2, 7).

### P-007

**Core Topic / Objective**

Replace expert selection of historical analogue days and improve 24-hour
load forecasts.

**Methodology / Data used**

A deep Q-network selects three days using calendar/weather states and a
load-similarity reward. Their loads and weather feed a backpropagation neural
network. Korean system load and weather support rolling 30-day training and
March–April 2018 testing against weighted-distance selection and LSTM models
(§§2–4.1).

**Key Findings**

Mean selection similarity was 0.9719 versus 0.9546 for weighted distance.
RL-BPNN MAPE was 1.3444%, versus 1.5351% for LSTM and 2.4829% for
weighted-distance BPNN (Tables 4–7).

**Limitations / Gaps**

The experiment uses realized target-day weather. The authors leave quantified
adaptation and broader RL/neural variants for future work. **Appraisal:** one
country, two test months and trial-and-error hyperparameters limit evidence of
operational robustness (§§4.1, 5).

### P-008

**Core Topic / Objective**

Improve next-day maximum-demand forecasts using graded holiday effects and
sequences of working and nonworking days (claim 1).

**Methodology / Data used**

The embodiment combines demand/weather history, fractional holiday values,
a bottleneck neural network compressing a 15-day calendar pattern, polynomial
multiple regression, a neural residual model and special-period corrections.
It describes a system, without specifying an evaluation dataset (¶¶0027–0070).

**Key Findings**

The patent asserts improved accuracy around weekends and holiday periods,
including New Year, Golden Week and Obon. These are claimed benefits, without
a controlled performance comparison. Figure 7 illustrates historical error
by date (¶¶0044–0045, 0077–0081).

**Limitations / Gaps**

**Appraisal:** no sample size, region, test period, baseline comparison or
numerical accuracy result is reported. Predictive benefit and generalizability
therefore remain unverified.

### P-009

**Core Topic / Objective**

Evaluate outdoor thermal-sensation indices in summer and winter; propose
temperature load (TL) and a simpler thermal sensation index (TSI).

**Methodology / Data used**

Field surveys in six Japanese cities (five in winter) collect temperature,
humidity, wind, globe temperature and sensation reports after five-minute
standing exposures. THI, WCI, SET*, TL and TSI are compared
(§5, Tables 1–6).

**Key Findings**

TSI had the highest correlations with reported sensation: 0.879 in summer and
0.715 in winter, versus 0.850/0.675 for SET* and 0.867/0.666 for TL.
WCI performed poorly below 1.8 m/s (Table 6, §§6.1–6.3).

**Limitations / Gaps**

The author treats ordinal responses as interval data. TSI assumes standard
posture/clothing and needs globe temperature; winter clothing variability
reduces correlation. **Appraisal:** fitting and assessment use the same survey,
without independent validation (pp. 665, 667, 669).

### P-010

**Core Topic / Objective**

The discomfort index is the named subject. The article's precise objective
could not be verified from the accessible publisher record.

**Methodology / Data used**

Not verified: the original full text was unavailable.

**Key Findings**

Not verified from the original article. No formulas or results are inferred
from later citations.

**Limitations / Gaps**

Review gap: the full text is needed to assess the paper's methods, findings
and limitations. Only its bibliographic record was verified on 2026-09-19.
