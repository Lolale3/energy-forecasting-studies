# Our 10 worst wind forecast days: what happened and what would have helped

**Forecast reviewed:** the day-ahead ERCOT wind forecast recommended by this study, an equal blend of GFS, ICON and ECMWF run through the same LightGBM power model. All predictions are out-of-sample (each made by a model trained only on earlier data), July 2025 to November 2026, 495 full days. MAE 1,670 MW.

## Summary

- **Most bad days are shared weather misses.** On 9 of the 10 worst days, all three weather models missed in the same direction. Blending, which cuts everyday error by about 15%, can't help on these days.
- **The biggest group: windy days the models called ordinary.** On 5 days, forecast winds sat near the middle of their usual range (34th-52nd percentile), but the fleet produced 13-18 GW, and we came in 3,400-5,300 MW low. Our power model wasn't the problem: across wind levels it matches actual output to within a few percent.
- **One over-forecast was ice, not weather.** On Jan 24, 2026 (Winter Storm Fern), forecast winds were strong, but ERCOT reports about 13.5 GW of wind capacity unavailable because of icing. A weather-only forecast can't see that.
- **Two days were timing misses.** We forecast the right amount at the wrong time: a front about 3-4 hours off.
- **Two over-forecasts are unexplained.** Our data can't separate a weather miss from curtailment or outages.

## The worst days

The bad 10% of days carry 19% of all forecast error. Days with fewer than 20 hours of data are excluded.

| Day | Type | MAE (MW) | Bias (MW) | Evidence |
|---|---|---|---|---|
| 2026-03-14 | Too little wind | 5,301 | −5,301 | Forecast wind 7.3 m/s (50th pct.); actual 18.3 GW vs. 13.0 forecast; all 3 models low |
| 2026-01-24 | Too much wind (ice) | 4,804 | +4,804 | Winter Storm Fern; ~13.5 GW of wind capacity unavailable due to icing ([ERCOT](https://www.ercot.com/files/docs/2026/02/13/ERCOT-Monthly-January-2026-FINAL.pdf)) |
| 2026-03-01 | Too little wind | 4,463 | −4,463 | Forecast wind 7.5 m/s (52nd pct.); all 3 models low |
| 2025-12-04 | Too little wind | 4,206 | −3,355 | Evening ramp from 10 to 20 GW against a forecast of 6 to 9 GW; then 24 hours of actuals missing in EIA-930 |
| 2026-10-04 | Too much wind (unexplained) | 4,156 | +4,156 | Light-wind day (29th pct.); 5.2 GW actual vs. 9.3 forecast; no outside explanation found |
| 2026-06-19 | Timing | 3,832 | −288 | Little net bias; models disagreed; moving the forecast 3 h later cuts MAE to 3,211 |
| 2025-12-12 | Timing | 3,806 | −404 | Moving the forecast 4 h later cuts MAE to 2,796: it was about 4 hours early |
| 2026-02-13 | Too little wind | 3,782 | −3,782 | Forecast wind 6.9 m/s (45th pct.); all 3 models low |
| 2026-08-08 | Too little wind | 3,744 | −3,584 | Forecast wind 6.3 m/s (35th pct.); all 3 models low |
| 2026-10-02 | Too much wind (unexplained) | 3,649 | +3,510 | Forecast wind 7.9 m/s (61st pct.); 11.2 GW actual vs. 14.7 forecast; wind ran ~10% below market expectations that week ([Impedance Match](https://impedancematch.substack.com/p/ercot-weekly-5-oct-2026)) |

## Causes, and what would have helped

**1. Weather models underestimated strong wind (5 days, the largest group).**
I checked whether our power model squashed strong forecast winds. It didn't: on these days the forecast winds themselves were ordinary. Separately, binned by forecast wind speed, our MW matches actual MW to within about 1-4% at every level above 4 m/s. So the miss came from the weather models.
- *What would help:* updates closer to delivery and higher-resolution short-range models that capture fronts and low-level jets better, plus ensemble spread to flag days when "ordinary" might turn out not to be. More global models won't help, because they all missed together.

**2. Icing (Jan 24).**
Strong forecast winds, but turbines iced and went offline. This is the same pattern I found in turbine SCADA data, at grid scale.
- *What would help:* an icing adjustment driven by forecast temperature and precipitation type, plus ERCOT's outage information. One commercial forecaster that weekend came in *below* actual output ([Latitude Media](https://www.latitudemedia.com/news/armed-with-new-batteries-and-winterized-plants-ercot-survives-fern/)), which suggests icing-aware forecasts can do far better.

**3. Timing (Dec 12, Jun 19).**
Right total, wrong hours.
- *What would help:* intraday updates once the front's actual position is known; time-lagged or ensemble blending to soften sharp ramps.

**4. Unexplained over-forecasts (Oct 2, Oct 4).**
Could be weather models too high on light-wind days, or curtailment and outages. Output alone can't tell them apart.
- *What would help:* ERCOT curtailment and outage data, so operations can be separated from weather.

## Things to watch beyond the worst days

- **A small, steady under-forecast.** At most wind levels our forecast runs 2-4% low. That's consistent with fleet growth since the training period. Scaling by installed capacity, or retraining more often, should fix it.
- **Missing actuals.** EIA-930 wind output was missing for 24 hours on Dec 5, 2025, right after one of our worst misses. Monitoring for gaps in the actuals matters as much as monitoring the forecast.

## Caveats

- **Selection bias.** The day-2 forecast was better than day 1 on 8 of these 10 days, but these days were *chosen* because day 1 failed. Over all days, day 1 is clearly better (1,670 vs. 1,846 MW MAE). Don't judge a forecast on days picked for another forecast's failure.
- **No curtailment or outage data.** Without it, "too much wind" days can't be fully explained.
- **Evidence levels.** The ice explanation for Jan 24 rests on ERCOT's own report. "Weather models underestimated the wind" is inferred from forecast wind speeds and power, not from measured hub-height winds.
