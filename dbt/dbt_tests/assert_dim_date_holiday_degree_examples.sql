-- Worked examples of holiday_degree (休日度合い) from the real calendar, one
-- per rule of the design (docs/superpowers/specs/2026-09-05-dim-date-holiday-degree-design.md),
-- checked against the holiday seed with an independent Python reference on
-- 2026-09-05. A row fails when the date is missing or its degree differs.
with
  expected as (
  select * from values
    -- Type 1: a Saturday, and a national holiday that beats the period's
    -- first-day 0.8 (2025-04-29 昭和の日 opens ゴールデンウィーク).
    (date '2025-01-04', 0.8),
    (date '2025-04-29', 1.0),
    -- Type 2: the first day of 年末年始 and お盆 scores 0.8, their other days 1.0.
    (date '2024-12-30', 0.8),
    (date '2024-12-31', 1.0),
    (date '2025-01-03', 1.0),
    (date '2025-08-13', 0.8),
    -- Type 3, one sandwiched working day (飛び石連休の中日): between a Sunday
    -- and 山の日 (2026-08-10, the R-004 proximity day), between 山の日 and the
    -- first day of お盆 (2026-08-12), between 勤労感謝の日 and a Saturday
    -- (2023-11-24), between a Sunday and the first day of 年末年始 (2025-12-29).
    (date '2026-08-10', 0.5),
    (date '2026-08-12', 0.5),
    (date '2023-11-24', 0.5),
    (date '2025-12-29', 0.5),
    -- Type 3, two sandwiched working days (二飛び石連休の中日): between the 5/6
    -- 振替休日 and a Saturday.
    (date '2026-05-07', 0.3),
    (date '2026-05-08', 0.3),
    -- Three working days in a row score 0, even next to 文化の日 (2026-11-03,
    -- Tuesday; the Monday before it is a 0.5 day). A plain midweek day is 0.
    (date '2026-11-02', 0.5),
    (date '2026-11-04', 0),
    (date '2026-11-05', 0),
    (date '2026-11-06', 0),
    (date '2026-08-05', 0)
    as t(date_key, expected_holiday_degree)
  )

select
  expected.date_key,
  expected.expected_holiday_degree,
  dim_date.holiday_degree
from
  expected
  left join {{ ref('dim_date') }} as dim_date
    on dim_date.date_key = expected.date_key
where
  dim_date.holiday_degree is null
  or dim_date.holiday_degree <> expected.expected_holiday_degree
