# power-market-analytics

Power market analytics.

## Curated star schema

The curated layer (`dbt/models/curated/`) contains three fact tables across two
subject areas, sharing a conformed `dim_date`:

- `fct_jepx_spot_market` — market-wide JEPX day-ahead auction results, one row
  per delivery period (trade date × 30-minute time code).
- `fct_jepx_spot_area_price` — area clearing prices, one row per delivery
  period per bidding zone.
- `fct_jma_weather_hourly` — JMA hourly weather observations, one row per
  station and observation hour (native hourly grain; not interpolated to the
  30-minute JEPX periods — align by joining each delivery period to the
  weather hour that contains it).

```mermaid
erDiagram
    dim_date ||--o{ fct_jepx_spot_market : "date_key"
    dim_delivery_period ||--o{ fct_jepx_spot_market : "time_code"
    dim_date ||--o{ fct_jepx_spot_area_price : "date_key"
    dim_delivery_period ||--o{ fct_jepx_spot_area_price : "time_code"
    dim_area ||--o{ fct_jepx_spot_area_price : "area_key"
    dim_date ||--o{ fct_jma_weather_hourly : "date_key"
    dim_jma_station ||--o{ fct_jma_weather_hourly : "station_id"

    dim_date {
        date date_key PK
        int year
        int quarter
        int month
        int day_of_month
        int day_of_week_iso
        string day_name
        string month_name
        int fiscal_year
        int fiscal_quarter
        boolean is_weekend
        boolean is_holiday
        string holiday_name_ja
        boolean is_business_day
    }

    dim_delivery_period {
        int time_code PK
        int start_minute_of_day
        int hour_of_day
        string period_start_time
        string period_end_time
        boolean is_daytime
        string day_part
    }

    dim_area {
        int area_key PK
        string area_code
        string area_name_en
        string area_name_ja
        string tso_name_en
        string grid_frequency
        string grid_region
    }

    fct_jepx_spot_market {
        date date_key PK, FK
        int time_code PK, FK
        timestamp trade_datetime
        bigint sell_bid_volume_kwh
        bigint buy_bid_volume_kwh
        bigint contract_volume_kwh
        bigint sell_block_bid_volume_kwh
        bigint sell_block_contract_volume_kwh
        bigint buy_block_bid_volume_kwh
        bigint buy_block_contract_volume_kwh
        double system_price_jpy_kwh
    }

    fct_jepx_spot_area_price {
        date date_key PK, FK
        int time_code PK, FK
        int area_key PK, FK
        timestamp trade_datetime
        double area_price_jpy_kwh
    }

    dim_jma_station {
        string station_id PK
        string station_type
        int prefecture_code
        string station_name
        string station_kana
        double latitude
        double longitude
        double elevation_m
        string kansoku
        int obs_precipitation
        int obs_wind
        int obs_temperature
        int obs_sunshine
        int obs_snow
        int obs_other
        date observation_ended_on
        boolean is_active
    }

    fct_jma_weather_hourly {
        string station_id PK, FK
        timestamp observed_at PK
        timestamp observed_hour_start_at
        date date_key FK
        double precipitation_mm
        int precipitation_phenomenon_absent
        int precipitation_quality_flag
        int precipitation_homogeneity_no
        double temperature_c
        int temperature_quality_flag
        int temperature_homogeneity_no
        double wind_speed_ms
        int wind_speed_quality_flag
        string wind_direction
        int wind_direction_quality_flag
        int wind_homogeneity_no
        double sunshine_duration_h
        int sunshine_phenomenon_absent
        int sunshine_quality_flag
        int sunshine_homogeneity_no
    }

    classDef dim fill:#DBEAFE,stroke:#2563EB,color:#1E3A8A
    classDef fact fill:#FEF3C7,stroke:#B45309,color:#78350F
    class dim_date,dim_delivery_period,dim_area,dim_jma_station dim
    class fct_jepx_spot_market,fct_jepx_spot_area_price,fct_jma_weather_hourly fact
```

Notes:

- Prices (`system_price_jpy_kwh`, `area_price_jpy_kwh`) are non-additive —
  average them (volume-weighted if needed), never sum. Volumes are fully
  additive.
- `trade_datetime` is a standalone timestamp for time-series work, not a
  dimension key.
- `dim_area` row 0 is the default "System (Nationwide)" row, so fact tables
  never carry a null area foreign key.
- `dim_date` is conformed across both subject areas: its spine starts 2016-01-01
  to cover JMA weather (JEPX spot begins at fiscal year 2016 = 2016-04-01).
- `fct_jma_weather_hourly.observed_at` marks the end of the observation hour;
  precipitation and sunshine accumulate over `[observed_hour_start_at,
  observed_at]`, temperature and wind are instantaneous at `observed_at`.
  `phenomenon_absent` is null for AMeDAS stations; value 0 with
  `phenomenon_absent = 0` is a JMA "trace" reading (below measurement
  resolution), distinct from a true zero (`phenomenon_absent = 1`).

## Development environment

The project runs inside a Docker Compose stack (see `docker-compose.yaml`):

- **devcontainer** — Python 3.13 + uv + Spark client tooling; open the repo in VS Code and reopen in container
- **postgres-metastore** — backing store for the Hive Metastore (host port 5432)
- **postgres-mlflow** — backing store for MLflow (host port 5433)
- **hive-metastore** — standalone Hive Metastore backed by Postgres
- **thriftserver** — Spark Thrift Server (JDBC/ODBC, port 10000; Spark UI on 4040)
- **mlflow** — experiment tracking UI on port 5005
- **docsify** — serves `docs/` on port 3000

### Setup

1. Copy `.env.template` to `.env` and fill in the values (see the comments for per-host memory settings).
2. `docker compose up -d`
3. Open the repo in VS Code and use "Reopen in Container", or `docker compose exec devcontainer bash`.

### Running commands (`just`)

The `justfile` wraps `docker compose exec` so python and dbt commands run
inside the devcontainer from a host terminal (requires
[just](https://github.com/casey/just), e.g. `brew install just`, and the
compose stack to be up):

```bash
just refresh-jepx                        # JEPX refresh: redownload market data + holidays, reload raw, rebuild + test dbt
just refresh-jma --prefecture 44         # JMA weather refresh (scoped; no args = full network, ~60 h cold)
just python scripts/load_jepx_spot.py    # python in the devcontainer
just python -c "import power_market_analytics"
just dbt run                             # dbt, run from /workspace/dbt
just dbt test --select stg_jepx__spot
just exec spark-submit --version         # any command in the devcontainer
just sql                                 # beeline SQL shell on the thriftserver
just shell                               # interactive bash in the devcontainer
```

Run `just --list` to see all recipes. Anything creating a `SparkSession`
must run in the devcontainer (the Hive metastore and `/spark-warehouse`
volume only resolve on the compose network); dbt also works from the host
directly with `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt <command>`.
