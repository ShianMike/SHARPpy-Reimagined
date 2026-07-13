# SHARPpy Sounding Parameter Guide

This document serves as an exhaustive reference manual for all thermodynamic, kinematic, and composite parameters calculated and displayed within the SHARPpy Reimagined Skew-T sounding analysis system.

---

## 1. Convective / Thermodynamic Parcel Parameters

These parameters describe the properties of a parcel of air lifted from a specific starting height through the environmental column. In SHARPpy, these are calculated for four standard parcel types:
1.  **Surface-Based (SFCPCL):** Lifted starting from surface pressure, temperature, and dewpoint.
2.  **Mixed-Layer (MLPCL):** Lifted using the mean potential temperature and mixing ratio of the lowest $100\text{ hPa}$ layer.
3.  **Most Unstable (MUPCL):** Lifted from the pressure level within the lowest $300\text{ hPa}$ that possesses the highest equivalent potential temperature ($\theta_e$).
4.  **Forecast (FCSTPCL):** Lifted using a modified surface parcel where the surface temperature is set to the forecast maximum temperature, and moisture is mixed across the lowest boundary layer.

### Convective Available Potential Energy (CAPE)
*   **Physical Significance:** CAPE represents the integrated positive buoyancy of an air parcel. It measures the potential energy available to accelerate an updraft vertically. Higher CAPE implies stronger updrafts, which can support larger hail and more intense updraft rotation.
*   **Mathematical Formula:**
    $$CAPE = \int_{z_{LFC}}^{z_{EL}} g \left( \frac{T_{v, parcel} - T_{v, env}}{T_{v, env}} \right) dz$$
    Where:
    *   $g$ is acceleration due to gravity ($9.81\text{ m/s}^2$).
    *   $T_{v, parcel}$ is the virtual temperature of the parcel.
    *   $T_{v, env}$ is the virtual temperature of the environment.
    *   $z_{LFC}$ is the height of the Level of Free Convection (LFC).
    *   $z_{EL}$ is the height of the Equilibrium Level (EL).
*   **Implementation & Edge Cases:** All calculations use the virtual temperature correction, which accounts for the lower density of water vapor compared to dry air. If the parcel is not buoyant at any level (no LFC exists), CAPE is exactly $0\text{ J/kg}$.
*   **Color-Coding Tiers (from `colors.py`):**
    *   $\text{CAPE} \le 0\text{ J/kg}$: Neutral white (`#FFFFFF`)
    *   $0 < \text{CAPE} < 1000\text{ J/kg}$: Tier 2 (`#e0a800`) - Weak Instability
    *   $1000 \le \text{CAPE} < 2000\text{ J/kg}$: Tier 3 (`#ffff00`) - Moderate Instability
    *   $2000 \le \text{CAPE} < 3000\text{ J/kg}$: Tier 4 (`#ff0000`) - Strong Instability
    *   $3000 \le \text{CAPE} < 4000\text{ J/kg}$: Tier 5 (`#e700df`) - Very Strong Instability
    *   $\text{CAPE} \ge 4000\text{ J/kg}$: Tier 6 (`#ff00ff`) - Extreme Instability
*   **Reference:** Moncrieff, M. W., and M. J. Miller, 1976: The dynamics and simulation of deep convective clouds. *Quart. J. Roy. Meteor. Soc.*, 102, 373-394.

### Convective Inhibition (CIN)
*   **Physical Significance:** CIN measures the negative buoyancy that must be overcome to lift a parcel from its starting level to the LFC. It acts as a lid or "cap" on convective development. A small amount of CIN can prevent weak storms from forming, allowing instability to build up for explosive storm development later in the day.
*   **Mathematical Formula:**
    $$CIN = \int_{z_{start}}^{z_{LFC}} g \left( \frac{T_{v, parcel} - T_{v, env}}{T_{v, env}} \right) dz$$
    *(where the integrand is negative; CIN itself is reported as a negative number in J/kg).*
*   **Implementation Details:** The integration starts at the parcel's initial pressure level and goes up to the LFC. If the parcel has no CAPE, CIN is undefined (or set to $0\text{ J/kg}$).
*   **Color-Coding Tiers:**
    *   $\text{CIN} \ge -25\text{ J/kg}$: Tier 6 (`#ff00ff`) - Negligible Cap / Highly favorable for initiation
    *   $-50 \le \text{CIN} < -25\text{ J/kg}$: Tier 5 (`#e700df`) - Weak Cap
    *   $-75 \le \text{CIN} < -50\text{ J/kg}$: Tier 4 (`#ff0000`) - Moderate Cap
    *   $-125 \le \text{CIN} < -75\text{ J/kg}$: Tier 3 (`#ffff00`) - Strong Cap
    *   $\text{CIN} < -125\text{ J/kg}$: Tier 2 (`#e0a800`) - Very Strong / Hostile Cap (initiation unlikely without extreme forcing)
*   **Reference:** Colby, F. P. Jr., 1984: Convective inhibition as a predictor of convection during AVE-SESAME II. *Mon. Wea. Rev.*, 112, 2239-2252.

### Lifted Condensation Level (LCL)
*   **Physical Significance:** The height AGL where a dry adiabatically lifted parcel becomes saturated (Relative Humidity $= 100\%$). It represents the height of the cloud base. Lower cloud bases (low LCL heights) reduce evaporation rates of descending rain and outflow, which is strongly correlated with supercell tornadogenesis.
*   **Mathematical Formula:**
    First, find the LCL Temperature ($T_{LCL}$) using Bolton's formula:
    $$T_{LCL} = \frac{2840}{3.5 \ln(T_k) - \ln(e) - 4.805} + 55$$
    Where:
    *   $T_k$ is parcel temperature in Kelvin.
    *   $e$ is water vapor pressure in hPa.
    Once $T_{LCL}$ is determined, the LCL height ($z_{LCL}$) is found by lifting the parcel along the dry adiabat from the surface.
*   **Color-Coding Tiers:**
    *   $\text{LCL} < 750\text{ m AGL}$: Tier 6 (`#ff00ff`) - Very Low Cloud Base (Highly supportive of tornadoes)
    *   $750 \le \text{LCL} < 1000\text{ m AGL}$: Tier 5 (`#e700df`) - Low Cloud Base
    *   $1000 \le \text{LCL} < 1250\text{ m AGL}$: Tier 4 (`#ff0000`) - Moderate Cloud Base
    *   $1250 \le \text{LCL} < 1500\text{ m AGL}$: Tier 3 (`#ffff00`) - High Cloud Base
    *   $\text{LCL} \ge 1500\text{ m AGL}$: Tier 2 (`#e0a800`) - Very High Cloud Base (Strong evaporation / high outflow wind risk)
*   **Reference:** Bolton, D., 1980: The computation of equivalent potential temperature. *Mon. Wea. Rev.*, 108, 1046-1053.

### Level of Free Convection (LFC)
*   **Physical Significance:** The height where the virtual temperature of a moist adiabatically lifted parcel becomes greater than the environment. Once a parcel crosses this boundary, it rises on its own buoyancy without external lift.
*   **Mathematical Formula:**
    The lowest height $z > z_{LCL}$ where:
    $$T_{v, parcel}(z) > T_{v, env}(z)$$
*   **Reference:** Standard thermodynamic definition.

### Equilibrium Level (EL)
*   **Physical Significance:** The level at which the buoyant parcel becomes colder than the environment and stops rising. It marks the upper limit of convective growth (tops of storm clouds).
*   **Mathematical Formula:**
    The height $z > z_{LFC}$ where:
    $$T_{v, parcel}(z) = T_{v, env}(z)$$
*   **Reference:** Standard thermodynamic definition.

### Lifted Index (LI)
*   **Physical Significance:** A classic index of stability. It represents the temperature of the environment at $500\text{ hPa}$ minus the temperature of the parcel lifted to $500\text{ hPa}$.
*   **Mathematical Formula:**
    $$LI = T_{env, 500} - T_{parcel, 500} \quad (\text{in }^\circ\text{C})$$
*   **Color-Coding Tiers:**
    *   $\text{LI} < -13$: Tier 6 (`#ff00ff`) - Extreme Instability
    *   $-13 \le \text{LI} < -10$: Tier 5 (`#e700df`) - Very Strong Instability
    *   $-10 \le \text{LI} < -7$: Tier 4 (`#ff0000`) - Strong Instability
    *   $-7 \le \text{LI} < -4$: Tier 3 (`#ffff00`) - Moderate Instability
    *   $\text{LI} \ge -4$: Tier 2 (`#e0a800`) - Weak or no instability
*   **Reference:** Galway, J. G., 1956: The lifted index as a predictor of latent instability. *Bull. Amer. Meteor. Soc.*, 37, 528-529.

---

## 2. Convective / Thermodynamic Column Parameters

These parameters characterize the moisture, dryness, and stability profile of the atmospheric column.

### Precipitable Water (PWAT)
*   **Physical Significance:** The total depth of liquid water that would result if all water vapor in a column of the atmosphere were condensed and precipitated to the surface. It is a key indicator of heavy rain potential, storm precipitation efficiency, and atmospheric moisture loading.
*   **Mathematical Formula:**
    $$PWAT = \frac{1}{\rho_w g} \int_{p_{top}}^{p_{bot}} q(p) \, dp$$
    Where:
    *   $\rho_w$ is the density of liquid water ($1000\text{ kg/m}^3$).
    *   $q(p)$ is the water vapor mixing ratio at pressure level $p$.
    *   $p_{bot}$ is the surface pressure; $p_{top}$ is the top-of-sounding pressure.
*   **Reference:** Standard meteorological definition.

### Mean Water Vapor Mixing Ratio (MeanW)
*   **Physical Significance:** The average concentration of water vapor in the lowest portion of the atmosphere (typically SFC to $100\text{ hPa}$ above surface). It measures boundary-layer moisture content.
*   **Mathematical Formula:**
    $$\text{MeanW} = \frac{\int_{p_{sfc}-100}^{p_{sfc}} w(p) \, dp}{100\text{ hPa}}$$
    Where $w(p)$ is the water vapor mixing ratio ($\text{g/kg}$).
*   **Reference:** Standard boundary-layer meteorological parameter.

### Low-Level Relative Humidity (LowRH)
*   **Physical Significance:** The average relative humidity in the lowest boundary layer (SFC to SFC $- 100\text{ hPa}$). High values inhibit evaporation and favor lower convective cloud bases.
*   **Mathematical Formula:**
    $$\text{LowRH} = \frac{\int_{p_{sfc}-100}^{p_{sfc}} RH(p) \, dp}{100\text{ hPa}}$$
*   **Reference:** Standard thermodynamic parameter.

### Mid-Level Relative Humidity (MidRH)
*   **Physical Significance:** The average relative humidity in the middle troposphere (usually defined between $700\text{ hPa}$ and $500\text{ hPa}$). Low values represent dry layers which enhance microburst potential via evaporative cooling but can also entrain dry air into updrafts and weaken storm cores.
*   **Mathematical Formula:**
    $$\text{MidRH} = \frac{\int_{500}^{700} RH(p) \, dp}{200\text{ hPa}}$$
*   **Reference:** Standard thermodynamic parameter.

### Downdraft CAPE (DCAPE)
*   **Physical Significance:** Measures the potential energy of a cold, descending downdraft. High DCAPE is strongly associated with severe damaging wind gusts (microbursts/downbursts) because evaporation in dry mid-level air cools the air, making it denser than the surrounding environment.
*   **Mathematical Formula:**
    $$DCAPE = \int_{z_{sfc}}^{z_{min\theta_e}} g \left( \frac{T_{v, env} - T_{v, parcel}}{T_{v, env}} \right) dz$$
    Where the descending parcel is initialized from the level of minimum equivalent potential temperature ($\theta_e$) in the lowest $400\text{ hPa}$ and descends moist adiabatically to the surface.
*   **Reference:** Gilmore, M. S., and L. J. Wicker, 1998: The influence of midtropospheric dryness on supercell morphology and evolution. *Mon. Wea. Rev.*, 126, 943-958.

### Downrush Temperature (DownT)
*   **Physical Significance:** The theoretical temperature of the downdraft parcel when it reaches the surface, representing the surface cold pool temperature.
*   **Mathematical Formula:**
    $$\text{DownT} = T_{down, sfc} \quad (\text{converted to }^\circ\text{F})$$
    Calculated as the final temperature of the wet-bulb parcel lowered moist adiabatically from the minimum $\theta_e$ level to the surface.
*   **Reference:** Standard SHARPpy thermodynamic calculation.

### K-Index (K)
*   **Physical Significance:** A stability index used to predict the probability of thunderstorms. It combines vertical temperature lapse rate, moisture content of the lower troposphere, and the vertical extent of the moist layer.
*   **Mathematical Formula:**
    $$K = (T_{850} - T_{500}) + Td_{850} - (T_{700} - Td_{700})$$
    Where:
    *   $T$ and $Td$ are the temperature and dewpoint temperature ($^\circ\text{C}$) at the indicated pressure levels ($850, 700, 500\text{ hPa}$).
*   **Reference:** George, J. J., 1960: *Weather Forecasting for Aeronautics*. Academic Press, 673 pp.

### Totals-Totals Index (TT)
*   **Physical Significance:** A static stability index used to assess the potential for severe weather. It is the sum of Vertical Totals (VT) and Cross Totals (CT).
*   **Mathematical Formula:**
    $$TT = CT + VT$$
    $$CT = Td_{850} - T_{500}$$
    $$VT = T_{850} - T_{500}$$
    $$TT = T_{850} + Td_{850} - 2T_{500}$$
    Where all temperature values are in Celsius.
*   **Reference:** Miller, R. C., 1972: Notes on analysis and severe-storm forecasting procedures of the Air Force Global Weather Central. *Air Weather Service Tech. Report 200 (Rev)*, USAF.

### Convective Temperature (ConvT)
*   **Physical Significance:** The surface temperature that must be reached to initiate convective cloud development (onset of thermals) due to solar heating without the aid of mechanical lifting.
*   **Mathematical Formula:**
    The surface temperature corresponding to a parcel lifted dry adiabatically from the surface that intersects the environmental temperature profile exactly at its LCL (using the mean mixing ratio of the lowest boundary layer).
*   **Reference:** Standard thermodynamic definition.

### Maximum Temperature (MaxT)
*   **Physical Significance:** A forecast maximum temperature calculated by assuming a well-mixed boundary layer up to a specified pressure depth (default is $100\text{ hPa}$ above surface).
*   **Mathematical Formula:**
    $$\text{MaxT} = T_{env, mixlayer} \times \left( \frac{p_{sfc}}{p_{mixlayer}} \right)^{R/C_p} + 2\text{ K}$$
    Where $R/C_p \approx 0.286$ (dry adiabatic exponent).
*   **Reference:** Standard thermodynamic forecasting technique.

### Enhanced Stretching Parameter (ESP)
*   **Physical Significance:** Identifies environmental conditions where low-level buoyancy ($0\text{-}3\text{ km AGL MLCAPE}$) and steep low-level lapse rates are co-located, which favors vertical stretching of low-level vortices and enhances tornado potential.
*   **Mathematical Formula:**
    $$ESP = \left( \frac{\text{MLCAPE}_{0\text{-}3\text{km}}}{50\text{ J/kg}} \right) \times \left( \frac{\Gamma_{0\text{-}3\text{km}} - 7.0^\circ\text{C/km}}{1.0^\circ\text{C/km}} \right)$$
    *If $\Gamma_{0\text{-}3\text{km}} < 7.0^\circ\text{C/km}$ or $\text{MLCAPE} < 250\text{ J/kg}$, then $ESP = 0$.*
*   **Reference:** SPC Mesoanalysis Page (Low-level vertical stretching formulation).

### Wind Damage Parameter (WNDG)
*   **Physical Significance:** A composite index that identifies areas where large MLCAPE, steep low-level lapse rates, strong wind flow in the low-to-mid levels, and low MLCIN are co-located, indicating potential for damaging convective wind gusts.
*   **Mathematical Formula:**
    $$WNDG = \left(\frac{\text{MLCAPE}}{2000}\right) \times \left(\frac{\Gamma_{0\text{-}3\text{km}}}{9}\right) \times \left(\frac{\bar{V}_{1\text{-}3.5\text{km}}}{15}\right) \times \left(\frac{50 + \text{MLCIN}}{40}\right)$$
    Where:
    *   $\bar{V}_{1\text{-}3.5\text{km}}$ is the mean wind speed in the $1\text{-}3.5\text{ km AGL}$ layer ($\text{m/s}$).
    *   $\Gamma_{0\text{-}3\text{km}}$ is the lapse rate ($^\circ\text{C/km}$, capped at $\ge 7.0$).
    *   $\text{MLCIN}$ is convective inhibition (capped at $-50\text{ J/kg}$ minimum; i.e., values more negative than $-50$ are treated as $-50$ in the term).
*   **Reference:** SPC Mesoanalysis Page (Wind Damage Composite parameter).

### Theta-E Index (TEI)
*   **Physical Significance:** A measure of the potential instability in the lower atmosphere, representing the difference between the maximum equivalent potential temperature ($\theta_e$) and the minimum $\theta_e$ in the lowest $400\text{ hPa}$.
*   **Mathematical Formula:**
    $$TEI = \theta_{e, max} - \theta_{e, min} \quad (\text{in K, evaluated in the lowest } 400\text{ hPa AGL})$$
*   **Reference:** SPC Mesoanalysis Page.

### 3CAPE / 6CAPE
*   **Physical Significance:** Convective energy available in the lowest $3\text{ km}$ and $6\text{ km}$ AGL layers respectively. Low-level CAPE ($3\text{CAPE}$) is crucial for storm acceleration and tornadogenesis, as it facilitates rapid vertical stretching of low-level vorticity.
*   **Mathematical Formula:**
    $$3CAPE = \int_{z_{sfc}}^{z_{3\text{km}}} g \left( \frac{T_{v, parcel} - T_{v, env}}{T_{v, env}} \right) dz$$
    $$6CAPE = \int_{z_{sfc}}^{z_{6\text{km}}} g \left( \frac{T_{v, parcel} - T_{v, env}}{T_{v, env}} \right) dz$$
*   **Reference:** Standard thermodynamic layer integration.

### Microburst Composite Index (MBURST)
*   **Physical Significance:** A composite index indicating the likelihood of severe dry/wet microbursts based on a weighted sum of stability, moisture, and thermodynamic indicators.
*   **Mathematical Formula:**
    $$MBURST = TE + SBCAPE_{term} + SBLI_{term} + PWAT_{term} + DCAPE_{term} + LR03_{term} + VT_{term} + TED_{term}$$
    Where each term represents a threshold-based score (ranging from $-5$ to $+4$) derived from SBCAPE, SBLI, PWAT, DCAPE, low-level lapse rates, vertical totals, and theta-e differences.
*   **Reference:** Formulated by Chad Entremont NWS JAN (2014) / Rich Thompson (SPC).

### Significant Severe Parameter (SigSvr)
*   **Physical Significance:** Identifies environments capable of supporting significant severe convective events (hail $\ge 2\text{ in}$, convective wind gusts $\ge 65\text{ kt}$, or EF2+ tornadoes) based on the Craven-Brooks parameter.
*   **Mathematical Formula:**
    $$\text{SigSvr} = \text{MLCAPE} \times \text{Shear}_{0\text{-}6\text{km}} \quad (\text{in }\text{m}^3/\text{s}^3)$$
    Where $\text{Shear}_{0\text{-}6\text{km}}$ is the bulk wind difference magnitude between the surface and $6\text{ km AGL}$ ($\text{m/s}$). Values exceeding $20,000\text{ m}^3/\text{s}^3$ are highly supportive of significant severe weather.
*   **Reference:** Craven, J. P., and H. E. Brooks, 2004: Baseline climatology of significant tornadoes for the United States. *Wea. Forecasting*, 19, 797-805.

### Lapse Rates
*   **Physical Significance:** The rate of temperature decrease with height in specific atmospheric layers: SFC to $500\text{ m AGL}$, SFC to $1\text{ km AGL}$, SFC to $3\text{ km AGL}$, $850\text{-}500\text{ hPa}$, and $700\text{-}500\text{ hPa}$. Steeper lapse rates (closer to the dry adiabatic rate of $9.8^\circ\text{C/km}$) represent greater instability.
*   **Mathematical Formula:**
    $$\Gamma = -\frac{\partial T}{\partial z} \approx \frac{T_{bottom} - T_{top}}{z_{top} - z_{bottom}} \quad (\text{in }^\circ\text{C/km})$$
*   **Reference:** Standard thermodynamic parameter.

---

## 3. Severe Weather Composite Parameters

These composite indices combine thermodynamic and kinematic variables to identify environments supportive of specific convective storm modes (supercells, tornadoes, and derechos).

### Supercell Composite Parameter (SCP)
*   **Physical Significance:** A non-dimensional parameter designed to identify environmental conditions favorable for right-moving (or left-moving, when computed using left-mover parameters) supercells. It combines CAPE, effective storm-relative helicity, and effective bulk shear.
*   **Mathematical Formula:**
    $$SCP = \left( \frac{\text{MUCAPE}}{1000\text{ J/kg}} \right) \times \left( \frac{\text{eff\_SRH}}{50\text{ m}^2/\text{s}^2} \right) \times \left( \frac{\text{eff\_bulk\_shear}}{20\text{ m/s}} \right)$$
    Where:
    *   $\text{eff\_bulk\_shear}$ is the bulk wind difference magnitude between the effective inflow base and $50\%$ of the equilibrium level height. If it is $< 10\text{ m/s}$, the term becomes $0.0$; if it is $> 20\text{ m/s}$, it is capped at $20\text{ m/s}$.
*   **Reference:** Thompson, R. L., R. Edwards, J. A. Hart, K. L. Elmore, and P. Markowski, 2003: Close proximity soundings within supercell environments obtained from the Rapid Update Cycle. *Wea. Forecasting*, 18, 1243-1261.

### Significant Tornado Parameter (Effective Layer, with CIN) - STP(cin)
*   **Physical Significance:** A composite index used to identify environments favorable for significant tornadoes (EF2 or stronger) associated with supercells. This version incorporates convective inhibition (CIN) to filter out unstable environments that are capped.
*   **Mathematical Formula:**
    $$STP(cin) = \left( \frac{\text{MLCAPE}}{1500\text{ J/kg}} \right) \times \left( \frac{\text{eff\_SRH}_{0\text{-}1\text{km}}}{150\text{ m}^2/\text{s}^2} \right) \times \left( \frac{\text{eff\_bulk\_shear}}{20\text{ m/s}} \right) \times \left( \frac{2000 - \text{MLLCL}}{1000\text{ m}} \right) \times \left( \frac{\text{MLCIN} + 200\text{ J/kg}}{150\text{ J/kg}} \right)$$
    With the following constraints:
    *   $\text{eff\_bulk\_shear}$ is capped at $30\text{ m/s}$ (value term $= 1.5$) and set to $0$ if $< 12.5\text{ m/s}$.
    *   The $\text{MLLCL}$ term is set to $1.0$ if $\text{MLLCL} < 1000\text{ m}$, and $0.0$ if $\text{MLLCL} > 2000\text{ m}$.
    *   The $\text{MLCIN}$ term is set to $1.0$ if $\text{MLCIN} > -50\text{ J/kg}$, and $0.0$ if $\text{MLCIN} < -200\text{ J/kg}$.
*   **Reference:** Thompson, R. L., B. T. Smith, J. S. Grams, A. R. Dean, and C. Broyles, 2012: Convective modes for significant severe thunderstorms in the contiguous United States. Part II: Supercell and QLCS tornado environments. *Wea. Forecasting*, 27, 1136-1154.

### Significant Tornado Parameter (Fixed Layer) - STP(fix)
*   **Physical Significance:** The original formulation of the Significant Tornado Parameter, which uses fixed heights (0-1 km for SRH, 0-6 km for bulk shear) rather than dynamically calculated effective layers.
*   **Mathematical Formula:**
    $$STP(fix) = \left( \frac{\text{SBCAPE}}{1500\text{ J/kg}} \right) \times \left( \frac{2000 - \text{SBLCL}}{1000\text{ m}} \right) \times \left( \frac{\text{SRH}_{0\text{-}1\text{km}}}{150\text{ m}^2/\text{s}^2} \right) \times \left( \frac{\text{bulk\_shear}_{0\text{-}6\text{km}}{20\text{ m/s}} \right)$$
    Constraints:
    *   The $\text{SBLCL}$ term is set to $1.0$ if $\text{SBLCL} < 1000\text{ m}$, and $0.0$ if $\text{SBLCL} > 2000\text{ m}$.
    *   $\text{bulk\_shear}_{0\text{-}6\text{km}}$ is capped at $30\text{ m/s}$ and set to $0$ if $< 12.5\text{ m/s}$.
*   **Reference:** Thompson, R. L., R. Edwards, J. A. Hart, K. L. Elmore, and P. Markowski, 2003: Close proximity soundings within supercell environments obtained from the Rapid Update Cycle. *Wea. Forecasting*, 18, 1243-1261.

### Significant Hail Parameter (SHIP)
*   **Physical Significance:** A composite index developed by the Storm Prediction Center to estimate the probability of significant hail ($\ge 2\text{ in}$ diameter).
*   **Mathematical Formula:**
    $$SHIP = -\frac{\text{MUCAPE} \times w_{mu} \times \Gamma_{700-500} \times T_{500} \times \text{Shear}_{0-6km}}{4.2 \times 10^7}$$
    Where:
    *   $w_{mu}$ is the mixing ratio of the MU parcel ($\text{g/kg}$), clamped between $11.0$ and $13.6$.
    *   $\Gamma_{700-500}$ is the $700\text{-}500\text{ hPa}$ lapse rate ($^\circ\text{C/km}$).
    *   $T_{500}$ is the $500\text{ hPa}$ temperature ($^\circ\text{C}$), capped at $-5.5$.
    *   $\text{Shear}_{0-6\text{km}}$ is the surface-to-6km bulk shear magnitude ($\text{m/s}$), clamped between $7.0$ and $27.0$.
    *   *Scaling:* SHIP is reduced linearly if $\text{MUCAPE} < 1300\text{ J/kg}$, if $\Gamma_{700-500} < 5.8^\circ\text{C/km}$, or if the freezing level height AGL is $< 2400\text{ m}$.
*   **Reference:** Johnson, A. U., and K. E. Sugden, 2014: An evaluation of the Significant Hail Parameter (SHIP) in forecasting large hail. *Electronic J. Severe Storms Meteor.*, 9 (1), 1-16.

### Derecho Composite Parameter (DCP)
*   **Physical Significance:** Designed to identify environmental conditions favorable for long-lived, severe convective windstorms (derechos). It combines convective instability (MUCAPE), downdraft strength (DCAPE), bulk shear, and mean wind speed.
*   **Mathematical Formula:**
    $$DCP = \left( \frac{\text{DCAPE}}{980\text{ J/kg}} \right) \times \left( \frac{\text{MUCAPE}}{2000\text{ J/kg}} \right) \times \left( \frac{\text{Shear}_{0\text{-}6\text{km, kt}}}{20\text{ kt}} \right) \times \left( \frac{\text{MeanWind}_{0\text{-}6\text{km, kt}}}{16\text{ kt}} \right)$$
    *(If DCAPE or MUCAPE is $0$, DCP is exactly $0.0$).*
*   **Reference:** Evans, J. S., and C. A. Doswell III, 2001: Examining derecho environments using proximity soundings. *Wea. Forecasting*, 16, 329-342.

---

## 4. Kinematic Parameters

These parameters measure the environmental wind shear, mean flow, storm-relative wind, and storm motion.

### Storm-Relative Helicity (SRH)
*   **Physical Significance:** SRH measures the potential for cyclonic updraft rotation (mesocyclones) in supercell thunderstorms. It represents the integration of streamwise vorticity in the storm-relative inflow layer (computed over $0\text{-}500\text{ m}$, $0\text{-}1\text{ km}$, $0\text{-}3\text{ km}$, and Effective Inflow layers).
*   **Mathematical Formula:**
    $$SRH = \int_{z_{bot}}^{z_{top}} \left( \vec{V}(z) - \vec{C} \right) \cdot \left( \vec{k} \times \frac{\partial \vec{V}}{\partial z} \right) dz$$
    Where:
    *   $\vec{V}(z)$ is the horizontal wind vector at height $z$.
    *   $\vec{C}$ is the storm motion vector (typically Bunkers Right-Mover).
    *   $\vec{k}$ is the vertical unit vector.
*   **Reference:** Davies-Jones, R., D. W. Burgess, and M. Foster, 1990: Test of helicity as a tornado predictor. *Preprints, 16th Conf. on Severe Local Storms*, Kananaskis Park, AB, Canada, Amer. Meteor. Soc., 588-592.

### Wind Shear (Bulk Shear)
*   **Physical Significance:** Represents the change in wind speed and direction with height between two levels. Strong vertical wind shear organizes convection, supports supercells, and prevents updrafts from being choked by their own downdraft rainfall.
*   **Mathematical Formula:**
    $$\Delta \vec{V} = \vec{V}(z_{top}) - \vec{V}(z_{bot})$$
    $$\text{Shear Magnitude} = |\Delta \vec{V}| = \sqrt{(u_{top} - u_{bot})^2 + (v_{top} - v_{bot})^2}$$
*   **Reference:** Standard kinematic definition.

### Mean Wind (MnWind)
*   **Physical Significance:** The pressure-weighted average wind vector in a specified layer. It represents the steering flow for convective systems.
*   **Mathematical Formula:**
    $$\vec{V}_{mean} = \frac{\sum (p_{i} - p_{i+1}) \vec{V}_i}{\sum (p_i - p_{i+1})}$$
*   **Reference:** Standard kinematic definition.

### Storm-Relative Wind (SRW)
*   **Physical Significance:** The environmental wind vector at a given level minus the storm motion vector. Strong storm-relative winds in the middle-to-upper levels ($4\text{-}6\text{ km}$) carry precipitation away from the updraft, preventing water loading and storm collapse.
*   **Mathematical Formula:**
    $$\vec{V}_{SR}(z) = \vec{V}(z) - \vec{C}$$
    Where $\vec{C}$ is the storm motion vector.
*   **Reference:** Thompson, R. L., R. Edwards, and Mead, 2007: Effective storm-relative helicity and bulk shear in supercell thunderstorm environments. *Wea. Forecasting*, 22, 102-115.

### Bulk Richardson Number Shear (BRN Shear)
*   **Physical Significance:** Measures the vertical wind shear in the lowest $6\text{ km}$ of the atmosphere, used to normalize CAPE in the Bulk Richardson Number ($BRN$). It represents the denominator of the BRN stability ratio.
*   **Mathematical Formula:**
    $$\text{BRN Shear} = \frac{|\vec{V}_{mean, 0\text{-}6\text{km}} - \vec{V}_{mean, 0\text{-}500\text{m}}|^2}{2} \quad (\text{in }\text{m}^2/\text{s}^2)$$
    $$BRN = \frac{CAPE}{\text{BRN Shear}}$$
*   **Reference:** Weisman, M. L., and J. B. Klemp, 1982: The dependence of numerically simulated convective storms on vertical wind shear and buoyancy. *Mon. Wea. Rev.*, 110, 504-520.

### Bunkers Storm Motion Vectors
*   **Physical Significance:** Establishes the expected propagation vectors for right-moving and left-moving supercells based on the mean wind and shear profiles.
*   **Mathematical Formula:**
    $$\vec{C}_R = \vec{V}_{mean} + D \frac{\vec{k} \times \vec{S}}{|\vec{S}|}$$
    $$\vec{C}_L = \vec{V}_{mean} - D \frac{\vec{k} \times \vec{S}}{|\vec{S}|}$$
    Where:
    *   $\vec{V}_{mean}$ is the pressure-weighted mean wind in the $0\text{-}6\text{ km}$ layer (or effective layer).
    *   $\vec{S}$ is the shear vector over the same layer.
    *   $D$ is a deviation constant ($7.5\text{ m/s}$, or $14.6\text{ kt}$).
*   **Reference:** Bunkers, M. J., B. A. Klimowski, J. W. Zeitler, and L. A. Thompson, 2000: Predicting supercell motion using a new hodograph technique. *Wea. Forecasting*, 15, 31-48.

### Corfidi Storm Motion Vectors
*   **Physical Significance:** Predicts the movement of Mesoscale Convective Systems (MCS) by combining the tropospheric flow (advection) and the low-level jet (propagation).
*   **Mathematical Formula:**
    *   **Upshear Vector:** $\vec{V}_{upshear} = \vec{V}_{mean, 850\text{-}300} - \vec{V}_{mean, SFC\text{-}1.5\text{km}}$
    *   **Downshear Vector:** $\vec{V}_{downshear} = \vec{V}_{mean, 850\text{-}300} + \vec{V}_{upshear}$
*   **Reference:** Corfidi, S. F., J. H. Merritt, and J. M. Fritsch, 1996: Predicting the movement of mesoscale convective complexes. *Wea. Forecasting*, 11, 41-46.

### Streamwiseness
*   **Physical Significance:** Measures the fraction of environmental horizontal vorticity that is streamwise (aligned with the storm-relative flow). High streamwiseness in the lowest $1\text{-}3\text{ km AGL}$ indicates that the inflow air possesses helical rotation, which is directly ingested by a storm's updraft to generate cyclonic rotation (mesocyclones) and enhance tornado potential.
*   **Mathematical Formula:**
    Let horizontal wind components at height $z$ be $u(z)$ and $v(z)$ (in m/s).
    Let storm-relative wind components be:
    $$u_{sr}(z) = u(z) - c_u$$
    $$v_{sr}(z) = v(z) - c_v$$
    Where $\vec{C} = (c_u, c_v)$ is the Bunkers storm motion vector.

    The components of environmental horizontal vorticity $\vec{\omega}_h = (\omega_u, \omega_v)$ (the horizontal components of the curl of the wind vector) are:
    $$\omega_u = -\frac{\partial v}{\partial z}$$
    $$\omega_v = \frac{\partial u}{\partial z}$$

    The total horizontal vorticity magnitude is:
    $$\omega_{mag} = \sqrt{\omega_u^2 + \omega_v^2}$$

    The storm-relative wind speed is:
    $$V_{sr} = \sqrt{u_{sr}^2 + v_{sr}^2}$$

    The streamwise component of vorticity (the scalar projection of horizontal vorticity onto the storm-relative wind direction) is:
    $$\omega_{streamwise} = \frac{\vec{\omega}_h \cdot \vec{V}_{sr}}{V_{sr}} = \omega_u \left( \frac{u_{sr}}{V_{sr}} \right) + \omega_v \left( \frac{v_{sr}}{V_{sr}} \right)$$

    Streamwiseness (expressed as a percentage) is:
    $$\text{Streamwiseness (\%)} = \left| \frac{\omega_{streamwise}}{\omega_{mag}} \right| \times 100\%$$

    The signed streamwiseness (preserving the sign for cyclonic/anticyclonic classification) is:
    $$\text{Signed Streamwiseness (\%)} = \text{sign}(\omega_{streamwise}) \times \text{Streamwiseness (\%)} = \frac{\omega_{streamwise}}{\omega_{mag}} \times 100\%$$
*   **Implementation & Interpolation:** The calculation is profile-agnostic and performed over a $100\text{ m}$ vertical grid up to $6\text{ km AGL}$. If the horizontal vorticity magnitude $\omega_{mag} < 10^{-6}\text{ s}^{-1}$ or the storm-relative speed $V_{sr} < 0.1\text{ m/s}$, the value is considered missing/undefined to avoid division by zero or noisy calculations in near-calm profiles.
*   **Visual Representation:** Shown on the streamwiseness chart as a percentage from $0\%$ to $100\%$, with color-shaded polygons representing cyclonic flow (red shading, $\omega_{streamwise} \ge 0$) and anticyclonic flow (blue shading, $\omega_{streamwise} < 0$).
*   **Reference:** Davies-Jones, R., 1984: Streamwise vorticity: The missing link in tornadogenesis. *Preprints, 13th Conf. on Severe Local Storms*, Kananaskis Park, AB, Canada, Amer. Meteor. Soc., 588-592.

---

## 5. Composite Board / Specialty Parameters

These metrics evaluate specific physical aspects of storm dynamics, including hail growth, entrainment, and low-level vorticity generation.

### Energy Helicity Index (EHI)
*   **Physical Significance:** Combines the convective potential (CAPE) with environmental rotation (SRH) into a single non-dimensional index. It identifies where severe rotating updrafts are most likely. Calculated for the $0\text{-}1\text{ km}$ and $0\text{-}3\text{ km}$ AGL layers.
*   **Mathematical Formula:**
    $$EHI = \frac{CAPE \times SRH}{160000}$$
*   **Reference:** Hart, J. A., and W. Korotky, 1991: The SHARP Workstation v1.50 User's Manual. National Weather Service, NOAA.

### Vorticity Generation Parameter (VGP)
*   **Physical Significance:** Evaluates the rate of environmental tilting and vertical stretching of horizontal vorticity. High values suggest a strong potential for supercell rotation and tornadogenesis.
*   **Mathematical Formula:**
    $$VGP = \sqrt{SBCAPE} \times \left( \frac{|\vec{V}_{4\text{km}} - \vec{V}_{sfc}|}{4000\text{ m}} \right)$$
    Where wind speeds are in $\text{m/s}$, yielding units of $\text{m/s}^2$.
*   **Reference:** Rasmussen, E. N., and R. B. Wilhelmson, 1983: Relationships between storm characteristics and 1200 GMT soundings. *Preprints, 13th Conf. on Severe Local Storms*, AMS, 112-115.

### Peskov Index
*   **Physical Significance:** An instability index utilized in convective weather forecasting that combines the George K-index, convective buoyancy, and mid-level dryness.
*   **Mathematical Formula:**
    $$\text{Peskov} = K_{index} + \frac{\text{SBCAPE}}{1000\text{ J/kg}} - \frac{\text{DD}_{700}}{5}$$
    Where $\text{DD}_{700}$ is the dewpoint depression ($T - T_d$) at $700\text{ hPa}$ ($^\circ\text{C}$).
*   **Reference:** Reviewed in Russian Meteorology and Hydrology, 39(5), 2014.

### MCS Maintenance Probability (MCS Index)
*   **Physical Significance:** Predicts the probability that an existing Mesoscale Convective System will maintain severe-level peak intensity over the next hour based on linear regression.
*   **Mathematical Formula:**
    $$\text{MCS Index (logit)} = a_0 + a_1 V_{max\_bulk\_shear} + a_2 \Gamma_{3\text{-}8\text{km}} + a_3 \text{MUCAPE} + a_4 \bar{V}_{3\text{-}12\text{km}}$$
    Where:
    *   $a_0 = 13.0, a_1 = -0.0459, a_2 = -1.16, a_3 = -0.000617, a_4 = -0.17$
    *   $V_{max\_bulk\_shear}$ is the maximum bulk wind shear magnitude between the lowest $1\text{ km}$ and the $6\text{-}10\text{ km}$ layers ($\text{m/s}$).
    *   $\Gamma_{3\text{-}8\text{km}}$ is the $3\text{-}8\text{ km AGL}$ lapse rate ($^\circ\text{C/km}$).
    *   $\bar{V}_{3\text{-}12\text{km}}$ is the mean wind speed in the $3\text{-}12\text{ km}$ layer ($\text{m/s}$).
    *   *Probability:* $MMP = \frac{1}{1 + e^{\text{MCS Index}}}$
*   **Reference:** Coniglio, M. C., D. J. Stensrud, and L. J. Wicker, 2006: Effects of upper-level shear on the structure and maintenance of strong quasi-linear mesoscale convective systems. *J. Atmos. Sci.*, 63, 1231-1251.

### Severe Weather Threat Index (SWEAT)
*   **Physical Significance:** A static index developed by the U.S. Air Force to evaluate the severe weather threat by combining thermodynamic instability (Total Totals) and kinematic shear terms.
*   **Mathematical Formula:**
    $$SWEAT = 12 Td_{850} + 20 (TT - 49) + 2 V_{850} + V_{500} + 125 \left[ \sin(dir_{500} - dir_{850}) + 0.2 \right]$$
    With the following constraints:
    *   If $Td_{850} < 0$, the first term is $0$.
    *   If $TT < 49$, the second term is $0$.
    *   The last term (directional shear term) is only added if: $130^\circ \le dir_{850} \le 250^\circ$, $210^\circ \le dir_{500} \le 310^\circ$, $(dir_{500} - dir_{850}) > 0$, and both wind speeds $V_{850}, V_{500} \ge 15\text{ kt}$.
*   **Reference:** Miller, R. C., 1972: Notes on analysis and severe-storm forecasting procedures of the Air Force Global Weather Central. *Air Weather Service Tech. Report 200 (Rev)*, USAF.

### Modified SHERBE (MOSHE)
*   **Physical Significance:** A kinematic-thermodynamic index optimized for high-shear, low-CAPE (HSLC) severe weather environments.
*   **Mathematical Formula:**
    $$\text{Mod SHERBE} = \frac{(\Gamma_{0\text{-}3\text{km}} - 4)^2}{4} \times \frac{\text{Shear}_{0\text{-}1.5\text{km}} - 8}{10} \times \frac{\text{eff\_Shear} - 8}{10} \times \frac{w_{max} + 10}{9}$$
    Where:
    *   Lapse rate is in $^\circ\text{C/km}$; shears are in $\text{m/s}$.
    *   $w_{max}$ is the maximum theta-e vertical velocity.
*   **Reference:** Sherburn, K. B., and M. D. Parker, 2016: Climatology and ingredients of significant severe convection in high-shear, low-CAPE environments. *Wea. Forecasting*, 31, 563-586.

### Large Hail Parameter (LRGHAIL)
*   **Physical Significance:** A multi-parameter composite parameter used by the SPC to identify environments supportive of large hail ($\ge 2\text{ in}$ diameter) based on updraft strength, hail growth zone depth, and storm-relative shear angles.
*   **Mathematical Formula:**
    $$\text{LRGHAIL} = Term_A \times Term_B + 5$$
    Where:
    *   $Term_A$ combines $\text{MUCAPE}$, the depth of the Hail Growth Zone ($-10^\circ\text{C}$ to $-30^\circ\text{C}$ layer), and the $700\text{-}500\text{ hPa}$ lapse rate.
    *   $Term_B$ combines deep shear and storm-relative wind direction changes in the low-to-mid levels.
*   **Reference:** Johnson, A. U., and K. E. Sugden, 2014: An evaluation of the Significant Hail Parameter (SHIP) in forecasting large hail. *Electronic J. Severe Storms Meteor.*, 9 (1), 1-16.

### Hail Growth Zone CAPE (HGZ CAPE)
*   **Physical Significance:** Integrates the convective available potential energy specifically within the Hail Growth Zone (the temperature layer between $-10^\circ\text{C}$ and $-30^\circ\text{C}$). High CAPE in this zone accelerates updrafts where supercooled water droplets and hail embryos coexist, maximizing hail growth rates.
*   **Mathematical Formula:**
    $$HGZ\ CAPE = \int_{z(-10^\circ\text{C})}^{z(-30^\circ\text{C})} g \left( \frac{T_{v, parcel} - T_{v, env}}{T_{v, env}} \right) dz$$
*   **Reference:** Standard thermodynamic layer integration.

### Nontornadic Supercell Tornado Parameter (NSTP)
*   **Physical Significance:** A diagnostic composite index designed to differentiate between tornadic and nontornadic supercells by combining low-level lapse rates, low-level CAPE, deep shear, and surface-relative vorticity.
*   **Mathematical Formula:**
    $$NSTP = \left( \frac{\Gamma_{0\text{-}1\text{km}}}{9^\circ\text{C/km}} \right) \times \left( \frac{\text{MLCAPE}_{0\text{-}3\text{km}}}{100\text{ J/kg}} \right) \times \left( \frac{225 - \text{MLCIN}}{200\text{ J/kg}} \right) \times \left( \frac{18 - \text{Shear}_{0\text{-}6\text{km, ms}}}{5\text{ m/s}} \right) \times \left( \frac{\zeta_{sfc}}{8 \times 10^{-5}\text{ s}^{-1}} \right)$$
    Where $\zeta_{sfc}$ is the surface-relative vorticity.
*   **Reference:** Baumgardt, A. J., and A. R. Cook: SPC Mesoanalysis Page.

### Normalized CAPE (NCAPE)
*   **Physical Significance:** Normalizes CAPE by the depth of the buoyant layer. A higher NCAPE implies a fat CAPE profile (stronger acceleration over a smaller depth), suggesting explosive updraft growth.
*   **Mathematical Formula:**
    $$NCAPE = \frac{\text{MUCAPE}}{z_{EL} - z_{LFC}} \quad (\text{in }\text{J/kg/m})$$
*   **Reference:** Blanchard, D. O., 1998: Assessing the vertical distribution of convective available potential energy. *Wea. Forecasting*, 13, 870-877.

### Entraining CAPE (ECAPE)
*   **Physical Significance:** A physically-based formulation of CAPE that accounts for environmental air entrainment into the updraft, providing a more realistic estimate of updraft energy than traditional undiluted CAPE.
*   **Mathematical Formula:**
    $$ECAPE = \frac{V_{sr}^2}{2} + \frac{-1 - \psi - 2a \cdot NCAPE}{4a} + \frac{\sqrt{(1 + \psi + 2a \cdot NCAPE)^2 + 8a(CAPE - \psi \cdot NCAPE)}}{4a}$$
    Where:
    *   $V_{sr}$ is the storm-relative wind speed ($\text{m/s}$).
    *   $a = \psi / V_{sr}^2$.
    *   $\psi$ is an entrainment rate parameter defined by the Equilibrium Level height ($\psi \approx 0.19 \times z_{EL}$).
*   **Reference:** Peters, J. M., D. R. Chavas, H. Morrison, and Coauthors, 2023: An analytic formula for entraining CAPE in mid-latitude storm environments. *J. Atmos. Sci.*, 80, 2403-2422.

### Left-Moving Supercell Composite Parameter (LSCP)
*   **Physical Significance:** Identifies environments favorable for left-moving supercells (which are favored when shear vectors veer with height or in environments with straight hodographs).
*   **Mathematical Formula:**
    $$LSCP = \left(\frac{\text{MUCAPE}}{1000\text{ J/kg}}\right) \times \left(\frac{\text{left\_ESRH}}{50\text{ m}^2/\text{s}^2}\right) \times \left(\frac{\text{clip}(EBWD, 10, 20)}{20\text{ m/s}}\right) \times \text{CIN\_term}$$
    Where the $\text{CIN\_term} = 1.0$ if $\text{MUCIN} \ge -40\text{ J/kg}$, and $-40 / \text{MUCIN}$ otherwise.
*   **Reference:** SPC Mesoanalysis Page (Left-moving supercell composite parameter).

### Wet-Bulb Zero Height (WBZ Height)
*   **Physical Significance:** The height above ground level (AGL) of the $0^\circ\text{C}$ wet-bulb temperature. It serves as an indicator of the melting level for falling hail; heights between $2200\text{ m}$ and $3200\text{ m}$ are most favorable for large hail reaching the ground.
*   **Mathematical Formula:**
    $$z_{WBZ} \text{ where } T_w(z) = 0.0^\circ\text{C}$$
*   **Reference:** Standard thermodynamic melting-level definition.

---

## 6. Parameter Color-Coding & Interpretation Tiers

The SHARPpy Reimagined renderer maps calculated parameters to distinct colors according to thermodynamic and severe-weather thresholds. This visually draws the forecaster's eye immediately to extreme or high-threat regions of the sounding.

The system uses a modernized **Alert Palette** containing 7 distinct steps:
*   **Tier 0:** `#775000` (Muted Dark Gold / Missing Indicator)
*   **Tier 1:** `#c8911f` (Amber / Muted Gold)
*   **Tier 2:** `#e0a800` (Yellow-Gold / Light Warning)
*   **Tier 3:** `#ffff00` (Pure Yellow / Moderate Instability)
*   **Tier 4:** `#ff0000` (Pure Red / Strong Threat)
*   **Tier 5:** `#e700df` (Deep Magenta / Critical Warning)
*   **Tier 6:** `#ff00ff` (Bright Pink / Extreme Severity)

### Thermodynamic Threshold Color Rules
*   **Convective Available Potential Energy (CAPE):**
    *   $\text{CAPE} \ge 4000\text{ J/kg} \rightarrow$ Tier 6 (`#ff00ff`)
    *   $3000 \le \text{CAPE} < 4000\text{ J/kg} \rightarrow$ Tier 5 (`#e700df`)
    *   $2000 \le \text{CAPE} < 3000\text{ J/kg} \rightarrow$ Tier 4 (`#ff0000`)
    *   $1000 \le \text{CAPE} < 2000\text{ J/kg} \rightarrow$ Tier 3 (`#ffff00`)
    *   $0 < \text{CAPE} < 1000\text{ J/kg} \rightarrow$ Tier 2 (`#e0a800`)
*   **Convective Inhibition (CIN):**
    *   $\text{CIN} \ge -25\text{ J/kg} \rightarrow$ Tier 6 (`#ff00ff`)
    *   $-50 \le \text{CIN} < -25\text{ J/kg} \rightarrow$ Tier 5 (`#e700df`)
    *   $-75 \le \text{CIN} < -50\text{ J/kg} \rightarrow$ Tier 4 (`#ff0000`)
    *   $-125 \le \text{CIN} < -75\text{ J/kg} \rightarrow$ Tier 3 (`#ffff00`)
    *   $\text{CIN} < -125\text{ J/kg} \rightarrow$ Tier 2 (`#e0a800`)
*   **Lifted Condensation Level (LCL):**
    *   $\text{LCL} < 750\text{ m AGL} \rightarrow$ Tier 6 (`#ff00ff`)
    *   $750 \le \text{LCL} < 1000\text{ m AGL} \rightarrow$ Tier 5 (`#e700df`)
    *   $1000 \le \text{LCL} < 1250\text{ m AGL} \rightarrow$ Tier 4 (`#ff0000`)
    *   $1250 \le \text{LCL} < 1500\text{ m AGL} \rightarrow$ Tier 3 (`#ffff00`)
    *   $\text{LCL} \ge 1500\text{ m AGL} \rightarrow$ Tier 2 (`#e0a800`)
*   **Lifted Index (LI):**
    *   $\text{LI} < -13 \rightarrow$ Tier 6 (`#ff00ff`)
    *   $-13 \le \text{LI} < -10 \rightarrow$ Tier 5 (`#e700df`)
    *   $-10 \le \text{LI} < -7 \rightarrow$ Tier 4 (`#ff0000`)
    *   $-7 \le \text{LI} < -4 \rightarrow$ Tier 3 (`#ffff00`)
    *   $\text{LI} \ge -4 \rightarrow$ Tier 2 (`#e0a800`)

### Composite Index Tier Rules
*   **Significant Tornado Parameter (Fixed-Layer) - STP(fix):**
    *   $\text{STP(fix)} \ge 7 \rightarrow$ Tier 6 (`#ff00ff`)
    *   $5 \le \text{STP(fix)} < 7 \rightarrow$ Tier 5 (`#e700df`)
    *   $2 \le \text{STP(fix)} < 5 \rightarrow$ Tier 4 (`#ff0000`)
    *   $1 \le \text{STP(fix)} < 2 \rightarrow$ Tier 3 (`#ffff00`)
    *   $0 < \text{STP(fix)} < 1 \rightarrow$ Tier 2 (`#e0a800`)
*   **Significant Tornado Parameter (w/ CIN) & Supercell Composite (SCP) Symmetric Scale:**
    *   $\text{Value} \ge 15.0 \rightarrow$ Tier 6 (`#ff00ff`)
    *   $10.0 \le \text{Value} < 15.0 \rightarrow$ Tier 5 (`#e700df`)
    *   $5.0 \le \text{Value} < 10.0 \rightarrow$ Tier 4 (`#ff0000`)
    *   $2.0 \le \text{Value} < 5.0 \rightarrow$ Tier 3 (`#ffff00`)
    *   $0.5 \le \text{Value} < 2.0 \rightarrow$ Tier 2 (`#e0a800`)
    *   $-0.5 \le \text{Value} < 0.5 \rightarrow$ Tier 1 (`#c8911f`)
    *   $\text{Value} < -0.5 \rightarrow$ Tier 0 (`#775000`)
*   **Significant Hail Parameter (SHIP):**
    *   $\text{SHIP} \ge 5.0 \rightarrow$ Tier 6 (`#ff00ff`)
    *   $3.0 \le \text{SHIP} < 5.0 \rightarrow$ Tier 5 (`#e700df`)
    *   $2.0 \le \text{SHIP} < 3.0 \rightarrow$ Tier 4 (`#ff0000`)
    *   $1.0 \le \text{SHIP} < 2.0 \rightarrow$ Tier 3 (`#ffff00`)
    *   $0.1 \le \text{SHIP} < 1.0 \rightarrow$ Tier 2 (`#e0a800`)
*   **Large Hail Parameter (LRGHAIL):**
    *   $\text{LRGHAIL} \ge 14.0 \rightarrow$ Tier 6 (`#ff00ff`)
    *   $10.0 \le \text{LRGHAIL} < 14.0 \rightarrow$ Tier 5 (`#e700df`)
    *   $7.0 \le \text{LRGHAIL} < 10.0 \rightarrow$ Tier 4 (`#ff0000`)
    *   $4.0 \le \text{LRGHAIL} < 7.0 \rightarrow$ Tier 3 (`#ffff00`)
    *   $2.0 \le \text{LRGHAIL} < 4.0 \rightarrow$ Tier 2 (`#e0a800`)

### Non-Alert-Palette Fixed Color Scales
*   **Lapse Rates ($^\circ\text{C/km}$):**
    *   $\Gamma \le 6.0^\circ\text{C/km} \rightarrow$ Green (`#00FF00`)
    *   $6.0 < \Gamma \le 7.0^\circ\text{C/km} \rightarrow$ Yellow (`#FFFF00`)
    *   $7.0 < \Gamma \le 8.0^\circ\text{C/km} \rightarrow$ Orange (`#FFA500`)
    *   $8.0 < \Gamma \le 9.0^\circ\text{C/km} \rightarrow$ Red (`#FF0000`)
    *   $\Gamma > 9.0^\circ\text{C/km} \rightarrow$ Magenta (`#FF00FF`)
*   **SWEAT (Severe Weather Threat) Index:**
    *   $\text{SWEAT} < 250 \rightarrow$ Light Blue (`#3399FF`)
    *   $250 \le \text{SWEAT} < 350 \rightarrow$ Neutral White (`#FFFFFF`)
    *   $350 \le \text{SWEAT} < 500 \rightarrow$ Yellow (`#FFFF00`)
    *   $500 \le \text{SWEAT} < 650 \rightarrow$ Red (`#FF0000`)
    *   $\text{SWEAT} \ge 650 \rightarrow$ Pink/Magenta (`#FF00FF`)

### Custom Gradient-Color Rules (White $\rightarrow$ Yellow $\rightarrow$ Red $\rightarrow$ Pink)
Other parameters (e.g., DCP, EHI, MCS Index, NSTP, Mod SHERBE, Peskov Index, HGZ CAPE, NCAPE, ECAPE) utilize a direct multi-step gradient logic mapping to pure secondary alert highlights:
*   **DCP:** Yellow $\ge 1.0$, Red $\ge 4.0$, Pink $\ge 6.0$
*   **EHI:** Yellow $\ge 1.0$, Red $\ge 2.0$, Pink $\ge 3.0$
*   **MCS / MCS Index:** Yellow $\ge 1.0$, Red $\ge 2.0$, Pink $\ge 3.0$
*   **NSTP:** Yellow $\ge 1.0$, Red $\ge 2.0$, Pink $\ge 4.0$
*   **Mod SHERBE:** Yellow $\ge 1.0$, Red $\ge 2.0$, Pink $\ge 3.0$
*   **Peskov:** Yellow $\ge 1.0$, Red $\ge 4.0$, Pink $\ge 7.0$
*   **HGZ CAPE:** Yellow $\ge 1000$, Red $\ge 2500$, Pink $\ge 4000$
*   **NCAPE:** Yellow $\ge 0.1$, Red $\ge 0.2$, Pink $\ge 0.3$
*   **ECAPE:** Yellow $\ge 1000$, Red $\ge 2500$, Pink $\ge 4000$
*   **LSCP:** Yellow $\le -1.0$, Red $\le -4.0$, Pink $\le -8.0$ (Symmetric negative scale)

---

## 7. Parcel Lifting and Moist/Dry Adiabatic Theory

To calculate parameters like LCL, LFC, EL, CAPE, and CIN, the thermodynamic engine simulates lifting an air parcel vertically. This process is governed by classical dry and moist adiabatic thermodynamics:

### 1. Dry Adiabatic Ascent (Below the LCL)
As a parcel rises from its starting height $z_{start}$ to the LCL, it does not exchange heat with its surroundings (adiabatic) and remains unsaturated.
*   **Temperature ($T$):** Decreases at the dry adiabatic lapse rate $\Gamma_d = g/C_p \approx 9.8^\circ\text{C/km}$ (or $9.76\text{ K/km}$).
*   **Mixing Ratio ($w$):** Remains constant because no water vapor is condensing.
*   **Potential Temperature ($\theta$):** Conserved (constant).
    $$\theta = T \left( \frac{1000\text{ hPa}}{p} \right)^{R/C_p}$$

### 2. Saturated/Moist Adiabatic Ascent (Above the LCL)
At the LCL, the relative humidity reaches $100\%$. Further ascent causes water vapor to condense into liquid droplets, releasing latent heat of condensation. This latent heating offsets some of the expansion cooling, resulting in a slower temperature decrease.
*   **Temperature ($T$):** Decreases at the moist adiabatic lapse rate $\Gamma_m$, which is variable and depends on temperature and pressure:
    $$\Gamma_m = g \left[ \frac{1 + \frac{L_v w_s}{R_d T}}{C_p + \frac{L_v^2 w_s \epsilon}{R_d T^2}} \right]$$
    Where:
    *   $L_v$ is latent heat of vaporization ($2.5 \times 10^6\text{ J/kg}$).
    *   $w_s$ is saturated mixing ratio.
    *   $R_d$ is dry air gas constant ($287\text{ J/kg}\cdot\text{K}$).
    *   $\epsilon \approx 0.622$ (ratio of molecular weights of water and dry air).
*   **Equivalent Potential Temperature ($\theta_e$):** Conserved (constant) during moist adiabatic ascent:
    $$\theta_e \approx \theta \exp \left( \frac{L_v w(T_{LCL})}{C_p T_{LCL}} \right)$$

---

## 8. The Effective Inflow Layer Concept

Traditional storm parameters use fixed height layers above ground level (e.g., $0\text{-}1\text{ km}$ or $0\text{-}3\text{ km}$). However, real convective storms do not always feed on surface air; they frequently ingest elevated layers of air, or are cut off from the surface by stable boundaries.

To address this, Thompson et al. (2007) introduced the **Effective Inflow Layer**, which dynamically identifies the actual layer of warm, unstable inflow air feeding the storm's updraft.

### Search Algorithm (as implemented in `params.py`)
The algorithm utilizes the Most Unstable parcel (MUPCL) as a guide. If the MUPCL has non-zero buoyancy ($\text{MUCAPE} > 0$), the engine searches upward from the surface to identify the boundaries of the layer where:
1.  **Convective Available Potential Energy (CAPE)** is $\ge 100\text{ J/kg}$.
2.  **Convective Inhibition (CIN)** is $> -250\text{ J/kg}$ (meaning inhibition is less than $250\text{ J/kg}$).

*   **Bottom of the Layer ($p_{bot}$):** Found by starting at the surface and moving upward. The first level that meets both criteria (CAPE $\ge 100$, CIN $> -250$) is designated as the bottom.
*   **Top of the Layer ($p_{top}$):** The search continues upward from the bottom level. The first level encountered that fails either criteria (CAPE $< 100$ or CIN $\le -250$) marks the top of the inflow layer.

### Significance in Calculations
The calculated effective layer boundaries ($p_{bot}$ and $p_{top}$) are then used as the integration limits for severe weather composite indices:
*   **Effective Storm-Relative Helicity (eff_SRH):** Integrated only within the effective inflow layer, avoiding the inclusion of surface-based shear that is trapped beneath a stable boundary.
*   **Effective Bulk Shear:** Calculated as the bulk wind difference magnitude between the effective inflow layer base ($p_{bot}$) and $50\%$ of the equilibrium level (EL) height.
*   **Symmetric Indices:** Both `STP(cin)` and `SCP` utilize effective layer parameters. This improves forecasting skill for elevated convective environments (common in nocturnal storm setups).
