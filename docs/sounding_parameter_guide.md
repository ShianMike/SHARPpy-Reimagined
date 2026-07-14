# SHARPpy Reimagined Sounding Parameter Guide

This guide describes what the application currently calculates and displays. It is implementation documentation, not a claim that every formula is the only meteorologically valid definition of that parameter.

## Scope and notation

The source of truth for this guide is the active calculation and rendering code:

- Standard SHARPpy parcel and profile calculations come from the installed `sharppy.sharptab` package.
- Reimagined calculations come from `sharpmod/sharptab/derived.py`, `params.py`, `winds.py`, and `ecape.py`.
- Display behavior comes from `sharpmod/viz/index_board.py`, `param_board.py`, and `streamwiseness.py`.
- Display colors come from the helper functions actually called in `sharpmod/colors.py` and the active board widgets. Some legacy threshold tables in `colors.py` do not match those draw-time helpers and are therefore not used here.

Unless stated otherwise:

- heights are above ground level (AGL);
- pressure is in hPa;
- temperature is in degrees Celsius, except where Kelvin is written explicitly;
- wind speed is in knots in the profile, then converted when a formula calls for m/s;
- CAPE and CIN are in J/kg;
- missing or masked values display as `--` in a neutral color;
- short expressions use inline GitHub math, such as $T_v$; and
- display equations use GitHub's fenced `math` syntax.

## Parcel definitions

The GUI and CLI expose six parcel keys:

| Key | Parcel | Implementation |
| --- | --- | --- |
| `SFC` | Surface based | Uses the observed surface pressure, temperature, and dewpoint. If the most-unstable parcel starts at the surface, SHARPpy reuses that parcel result. |
| `ML` | Mixed layer | Uses mean potential temperature and mean mixing ratio through the lowest 100 hPa. |
| `FCST` | Forecast surface | Uses the forecast maximum-temperature parcel constructed by upstream SHARPpy. |
| `MU` | Most unstable | Selects the largest equivalent-potential-temperature parcel in the lowest 300 hPa. |
| `EFF` | Effective | Uses mean potential temperature and mixing ratio through the effective inflow layer. If no effective layer exists, the application falls back to the surface parcel. |
| `USER` | User defined | Uses a pressure, temperature, and dewpoint supplied by the user. It is empty until defined. |

The selected default parcel controls the parcel trace emphasized on the Skew-T. It does not change the definitions of composites that explicitly require SBCAPE, MLCAPE, or MUCAPE.

## Parcel thermodynamics

### CAPE

SHARPpy integrates positive parcel buoyancy. Its standard parcel calculation applies a virtual-temperature correction.

```math
\mathrm{CAPE}=\int_{z_{LFC}}^{z_{EL}}
g\frac{T_{v,p}-T_{v,e}}{T_{v,e}}\,dz
\quad\text{where the integrand is positive.}
```

If no positive-buoyancy layer is found, CAPE is zero. The parcel object also stores layer-limited positive energy such as `b3km` and `b6km`.

### CIN

CIN is the sum of negative parcel buoyancy and is reported as a negative value.

```math
\mathrm{CIN}=\int_{z_{start}}^{z_{LFC}}
g\frac{T_{v,p}-T_{v,e}}{T_{v,e}}\,dz
\quad\text{where the integrand is negative.}
```

The upstream routine has two important conventions: it accumulates these negative layers only while pressure is greater than 500 hPa, and it returns zero CIN when its floored CAPE is zero.

### LCL

The installed SHARPpy routine does not use the Bolton logarithmic LCL formula. With parcel temperature $T$ and dewpoint $T_d$ in degrees Celsius, it computes

```math
s=T-T_d
```

```math
\Delta T=s\left[1.2185+0.001278T
+s\left(-0.00219+1.173\times10^{-5}s-5.2\times10^{-6}T\right)\right]
```

```math
T_{LCL}=T-\Delta T.
```

LCL pressure is then obtained by lifting dry adiabatically to $T_{LCL}$, and the pressure is converted to an AGL height for display.

### LFC and EL

The level of free convection (LFC) is the first level above the LCL where the parcel becomes positively buoyant. The equilibrium level (EL) is the later crossing where positive buoyancy ends. Both crossings use the parcel and environmental virtual-temperature traces from the upstream parcel integration.

### Maximum Parcel Level (MPL)

The maximum parcel level is the height above the equilibrium level where the
parcel's accumulated negative buoyancy has consumed the positive energy gained
below the EL. In the upstream parcel integration, the search continues above
the EL until the integrated negative area equals the parcel's CAPE. The result
is stored as `mplpres` and `mplhght`; the GUI shows MPL both beside the Skew-T
and in the main parcel table. MPL can be missing when the sounding does not
extend high enough for the energy balance to be reached.

### Lifted Index

The displayed lifted index is the environmental virtual temperature minus the lifted parcel virtual temperature at 500 hPa:

```math
\mathrm{LI}_{500}=T_{v,e}(500)-T_{v,p}(500).
```

More-negative values indicate a warmer, more buoyant parcel at 500 hPa.

## Moisture, stability, and column parameters

### Precipitable water (PWAT)

The default integration is from the surface to 400 hPa, or to the sounding top if the sounding is shallower. It is not automatically the entire atmospheric column. SHARPpy trapezoidally integrates mixing ratio $w$ in g/kg and pressure in hPa, then returns inches:

```math
\mathrm{PWAT}_{in}=0.00040173
\sum_i\frac{w_i+w_{i+1}}{2}\left(p_i-p_{i+1}\right).
```

### Mean mixing ratio

The default layer is the lowest 100 hPa. Mixing ratio is interpolated at 1-hPa intervals and averaged:

```math
\overline{w}=\operatorname{mean}\left(w(p_s),w(p_s-1),\ldots,w(p_s-100)\right).
```

### Low-level and mid-level relative humidity

Relative humidity is sampled every 1 hPa and averaged using pressure values as weights.

- `LowRH`: surface to surface minus 100 hPa.
- `MidRH`: surface minus 150 hPa to surface minus 350 hPa. It is not a fixed 700–500-hPa layer.

For samples $RH_i$ at pressures $p_i$:

```math
\overline{RH}=\frac{\sum_i p_i RH_i}{\sum_i p_i}.
```

### DCAPE and downrush temperature

The downdraft source is the level with the minimum 100-hPa layer-mean equivalent potential temperature in the lowest 400 hPa. A saturated parcel descends moist adiabatically from that level.

```math
\mathrm{DCAPE}=\int_{z_s}^{z_{source}}
g\frac{T_e-T_p}{T_e}\,dz.
```

Unlike the standard upward-parcel CAPE calculation, the upstream DCAPE routine does **not** apply a virtual-temperature correction. The final downdraft parcel temperature at the surface is stored as the downrush temperature and displayed in degrees Fahrenheit.

### K Index

```math
K=(T_{850}-T_{500})+T_{d,850}-(T_{700}-T_{d,700}).
```

### Total Totals

```math
\mathrm{TT}=T_{850}+T_{d,850}-2T_{500}.
```

### Convective temperature

SHARPpy uses the mean low-level mixing-ratio dewpoint and repeatedly warms a surface parcel until its CIN reaches the requested threshold, zero by default. It first checks whether warming by 25 degrees Celsius would be sufficient; if not, it returns a missing value. This is an iterative parcel calculation, not a single LCL-intersection formula.

### Forecast maximum temperature

For the default 100-hPa mixing depth, let $p_t=p_s-100$ hPa. The code adds 2 K to the observed temperature at $p_t$ before bringing it dry adiabatically to the surface:

```math
T_{max,C}=\left[T(p_t)+273.15+2\right]
\left(\frac{p_s}{p_t}\right)^{R_d/C_p}-273.15.
```

The profile display converts this result to degrees Fahrenheit.

### Lapse rates

```math
\Gamma=\frac{T_{bottom}-T_{top}}{z_{top}-z_{bottom}}
\quad[{}^\circ\mathrm{C/km}].
```

The boards display surface–500 m, surface–1 km, surface–3 km, 850–500 hPa, and 700–500 hPa lapse rates where available.

### Theta-E Index (TEI)

TEI is the equivalent-potential-temperature range in the lowest 400 hPa, not “400 hPa AGL”:

```math
\mathrm{TEI}=\max(\theta_e)-\min(\theta_e).
```

### ESP

```math
\mathrm{ESP}=\frac{\mathrm{MLCAPE}_{0-3\,km}}{50}
\left(\Gamma_{0-3\,km}-7\right).
```

It is zero unless total MLCAPE is at least 250 J/kg and the 0–3-km lapse rate is at least 7 degrees Celsius per km. The 250-J/kg gate applies to total MLCAPE, not to the 0–3-km CAPE term.

### Wind Damage Parameter (WNDG)

```math
\mathrm{WNDG}=\frac{\mathrm{MLCAPE}}{2000}
\frac{\Gamma_{0-3\,km}}{9}
\frac{\overline{V}_{1-3.5\,km}}{15}
\frac{50+\mathrm{MLCIN}}{40}.
```

Mean wind speed is in m/s. The value is zero when the 0–3-km lapse rate is below 7 degrees Celsius per km. MLCIN more negative than -50 J/kg is clamped to -50 J/kg.

### 3CAPE and the two 6CAPE values

The application contains two similarly labeled values that must not be conflated:

- The main parcel board uses the upstream mixed-layer parcel values `mlpcl.b3km` and `mlpcl.b6km`.
- The parameter board's custom `cape_0_6km`, also labeled `6CAPE`, integrates positive buoyancy of a **surface-based** parcel from the surface to 6 km AGL.

Both use positive-buoyancy energy only:

```math
\mathrm{CAPE}_{z_1-z_2}=\int_{z_1}^{z_2}
\max\left(0,g\frac{T_{v,p}-T_{v,e}}{T_{v,e}}\right)dz.
```

### Hail-growth-zone CAPE

The custom value is surface-based positive buoyancy in the environmental -10 to -30 degrees Celsius layer. The implementation obtains it as the difference between surface-to--30-degree and surface-to--10-degree cumulative CAPE, preventing a false elevated-parcel start at the lower boundary.

```math
\mathrm{HGZ\ CAPE}=\mathrm{CAPE}_{sfc\rightarrow -30^\circ C}
-\mathrm{CAPE}_{sfc\rightarrow -10^\circ C}.
```

### Significant Severe (SigSvr)

```math
\mathrm{SigSvr}=\mathrm{MLCAPE}\times \mathrm{BWD}_{0-6\,km},
```

where bulk wind difference is converted to m/s. The resulting units are cubic metres per cubic second.

### Microburst Composite (MBURST)

This is a project-exposed threshold score:

```math
\mathrm{MBURST}=TE+CAPE_t+LI_t+PWAT_t+DCAPE_t+LR_t+VT_t+TED_t,
```

clamped to a minimum of zero. The exact terms are:

| Term | Score |
| --- | --- |
| Surface $\theta_e$ | 1 at 355 K or greater; otherwise 0 |
| SBCAPE | less than 2000: -5; 2000–3299: 0; 3300–3699: 1; 3700–4299: 2; 4300 or greater: 4 |
| SBLI | greater than -7.5: 0; -7.5 to greater than -9: 1; -9 to greater than -10: 2; -10 or lower: 3 |
| PWAT | less than 1.5 in: -3; otherwise 0 |
| DCAPE | 1 only when PWAT is greater than 1.70 in and DCAPE is greater than 900; otherwise 0 |
| 0–3-km lapse rate | 1 above 8.4 degrees Celsius per km; otherwise 0 |
| Vertical Totals | less than 27: 0; 27–27.9: 1; 28–28.9: 2; 29 or greater: 3 |
| Lowest-400-hPa theta-e difference | 1 at 35 K or greater; otherwise 0 |

## Severe-weather composites

### Supercell Composite Parameter (SCP)

```math
\mathrm{SCP}=\frac{\mathrm{MUCAPE}}{1000}
\frac{\mathrm{ESRH}}{50}
\frac{\mathrm{EBWD}_{m/s}}{20}.
```

ESRH is helicity through the effective inflow layer. The EBWD factor is zero below 10 m/s and capped at 20 m/s.

### Significant Tornado Parameter with CIN

```math
\mathrm{STP}_{cin}=\frac{\mathrm{MLCAPE}}{1500}
\frac{\mathrm{ESRH}}{150}
\frac{\mathrm{EBWD}_{m/s}}{20}
F_{LCL}F_{CIN}.
```

The factors are:

```math
F_{LCL}=\begin{cases}
1,&z_{MLLCL}<1000\ \mathrm{m}\\
(2000-z_{MLLCL})/1000,&1000\le z_{MLLCL}\le2000\ \mathrm{m}\\
0,&z_{MLLCL}>2000\ \mathrm{m}
\end{cases}
```

```math
F_{CIN}=\begin{cases}
1,&\mathrm{MLCIN}>-50\\
(200+\mathrm{MLCIN})/150,&-200\le\mathrm{MLCIN}\le-50\\
0,&\mathrm{MLCIN}<-200.
\end{cases}
```

EBWD is zero below 12.5 m/s and capped so its normalized term cannot exceed 1.5 at 30 m/s. The final result is floored at zero.

### Fixed-layer Significant Tornado Parameter

```math
\mathrm{STP}_{fix}=\frac{\mathrm{SBCAPE}}{1500}
F_{LCL}\frac{\mathrm{SRH}_{0-1\,km}}{150}
\frac{\mathrm{BWD}_{0-6\,km,m/s}}{20}.
```

Here $F_{LCL}$ uses the surface-parcel LCL with the same 1000–2000-m ramp. The bulk-shear term is zero below 12.5 m/s and capped at 30 m/s.

### Significant Hail Parameter (SHIP)

```math
\mathrm{SHIP}=
-\frac{\mathrm{MUCAPE}\,w_{MU}\,\Gamma_{700-500}\,T_{500}\,
\mathrm{BWD}_{0-6\,km,m/s}}{42{,}000{,}000}.
```

The code clamps 0–6-km shear to 7–27 m/s, MU mixing ratio to 11–13.6 g/kg, and a 500-hPa temperature warmer than -5.5 degrees Celsius to -5.5. It scales the result down when MUCAPE is below 1300 J/kg, the 700–500-hPa lapse rate is below 5.8 degrees Celsius per km, or freezing height is below 2400 m.

### Derecho Composite Parameter (DCP)

```math
\mathrm{DCP}=\frac{\mathrm{DCAPE}}{980}
\frac{\mathrm{MUCAPE}}{2000}
\frac{\mathrm{BWD}_{0-6\,km,kt}}{20}
\frac{\overline{V}_{0-6\,km,kt}}{16}.
```

Unlike several other composites, both kinematic factors are in knots. The result is zero when valid DCAPE or MUCAPE is zero.

### Large Hail Parameter (LRGHAIL)

The parameter is inactive and returns zero unless MUCAPE is at least 400 J/kg and 0–6-km bulk shear is at least 14 m/s. Otherwise:

```math
A=\max\left[0,
\frac{\mathrm{MUCAPE}-2000}{1000}
+\frac{3200-D_{HGZ}}{500}
+\frac{\Gamma_{700-500}-6.5}{2}\right]
```

```math
B=\max\left[0,
\frac{S_{LPL-EL}-25}{5}
+\frac{\alpha_{growth,EL}+5}{20}
+\frac{\alpha_{SRW,mid}-80}{10}\right]
```

```math
\mathrm{LRGHAIL}=A B+5.
```

$D_{HGZ}$ is the -10 to -30 degrees Celsius layer depth in metres. $S_{LPL-EL}$ is parcel-origin-to-EL shear in m/s. The two angle terms are derived from parcel-growth and storm-relative-wind vectors; the code assigns -10 degrees when the EL growth angle exceeds 180 degrees.

### Hail Parameter Index (HPI)

The project-defined HPI combines hail-growth-zone CAPE with a wet-bulb-zero penalty:

```math
\mathrm{HPI}=\frac{\mathrm{HGZ\ CAPE}}{500}
\operatorname{clip}\left(1-\frac{\max(0,z_{WBZ}-3350)}{3350},0,1\right).
```

This is distinct from SHIP and LRGHAIL.

## Wind and storm-relative parameters

### Bulk wind difference

```math
\Delta\vec V=\vec V_{top}-\vec V_{bottom},
\qquad \mathrm{BWD}=\lVert\Delta\vec V\rVert.
```

### Pressure-weighted mean wind

The local `mean_wind` routine interpolates $u$ and $v$ every 1 hPa and passes the pressure samples themselves as NumPy weights:

```math
\overline u=\frac{\sum_i p_i u_i}{\sum_i p_i},
\qquad
\overline v=\frac{\sum_i p_i v_i}{\sum_i p_i}.
```

This exact implementation differs from a layer-integral formula weighted only by pressure thickness. `mean_wind_npw` is the separate non-pressure-weighted routine.

### Storm-relative wind

```math
\vec V_{SR}=\overline{\vec V}-\vec C,
```

where $\vec C$ is the selected storm motion.

### Storm-relative helicity

The code interpolates the hodograph over the requested height layer, converts to m/s, subtracts storm motion, and sums each discrete segment:

```math
\mathrm{SRH}=\sum_i\left(u_{i+1}v_i-u_iv_{i+1}\right).
```

It also retains separate positive and negative contributions.

### Surface–500-m diagnostics

The custom profile includes surface–500-m SRH, bulk shear, pressure-weighted mean wind, and pressure-weighted storm-relative wind. These use the cached right-moving storm motion unless the display explicitly requests the left mover.

### Bunkers storm motion

When a usable effective layer and MU equilibrium level exist, upstream SHARPpy uses a parcel-based layer:

- base: effective-inflow-layer base;
- top: base plus 65 percent of the height difference from that base to the MU equilibrium level; and
- deviation: 7.5 m/s perpendicular to the layer shear vector.

If that parcel-based construction is unavailable, it falls back to a surface–6-km mean wind and shear with the same 7.5-m/s deviation.

### Effective bulk wind difference (EBWD)

EBWD is the bulk wind difference from the effective-layer base to the midpoint in height between that base and the MU equilibrium level. It is not simply the wind difference to one-half of the EL height above ground.

### Corfidi vectors

The routine uses non-pressure-weighted means:

```math
\vec C_{up}=\overline{\vec V}_{850-300\,hPa}
-\overline{\vec V}_{sfc-1.5\,km},
```

```math
\vec C_{down}=\overline{\vec V}_{850-300\,hPa}+\vec C_{up}.
```

### Bulk Richardson Number

The shear denominator uses the vector difference between the surface–500-m mean wind and surface–6-km mean wind:

```math
E_{shear}=\frac{1}{2}\left\lVert
\overline{\vec V}_{0-6\,km}-\overline{\vec V}_{0-500\,m}
\right\rVert_{m/s}^{2},
```

```math
\mathrm{BRN}=\frac{\mathrm{CAPE}}{E_{shear}}.
```

### Streamwiseness

The streamwiseness panel interpolates winds to a 100-m AGL grid through at most 6 km. It computes horizontal vorticity from the vertical wind gradients,

```math
\vec\omega_h=\left(-\frac{\partial v}{\partial z},
\frac{\partial u}{\partial z}\right),
```

then projects it onto storm-relative flow. The absolute projection ratio is clipped to 0–100 percent, while the signed value distinguishes positive/cyclonic (red) from negative/anticyclonic (blue). A point is usable only when vorticity magnitude exceeds $10^{-6}$ per second and storm-relative speed exceeds 0.1 m/s.

## Other custom and specialty parameters

### Energy-Helicity Index (EHI)

The local values use surface-based CAPE with right-moving SRH:

```math
\mathrm{EHI}_{0-h}=\frac{\mathrm{SBCAPE}\,\mathrm{SRH}_{0-h}}{160000},
\qquad h\in\{1\,\mathrm{km},3\,\mathrm{km}\}.
```

### Vorticity Generation Parameter (VGP)

```math
\mathrm{VGP}=\sqrt{\mathrm{SBCAPE}}
\frac{\mathrm{BWD}_{0-4\,km,m/s}}{4000\,\mathrm m}.
```

The output has units of m/s squared.

### Peskov Index

The repository implements this exact project-defined surrogate:

```math
\mathrm{Peskov}=K+\frac{\mathrm{SBCAPE}}{1000}
-\frac{T_{700}-T_{d,700}}{5}.
```

The code comments do not establish an authoritative historical published formula, so this guide does not present it as one.

### MCS index and MMP

Two related quantities appear in the application:

1. Custom `mcs_index` is the linear predictor (logit), not a probability:

```math
I=13-0.0459S_{max,m/s}-1.16\Gamma_{3-8\,km}
-0.000617\mathrm{MUCAPE}-0.17\overline V_{3-12\,km,m/s}.
```

2. Upstream `mmp` converts that predictor to a probability-like value:

```math
\mathrm{MMP}=\frac{1}{1+e^I}.
```

Panels labeled `MCS` that read `mcs_index` therefore show $I$, while a panel reading `mmp` shows the transformed value.

### SWEAT

```math
\mathrm{SWEAT}=12T_{d,850}+20(\mathrm{TT}-49)
+2f_{850}+f_{500}
+125\left[\sin(d_{500}-d_{850})+0.2\right].
```

Negative dewpoint and Total-Totals terms are set to zero. The directional term is used only when the 850-hPa direction is 130–250 degrees, the 500-hPa direction is 210–310 degrees, the directional difference is positive, and both wind speeds are at least 15 kt.

### Modified SHERBE (MOSHE)

```math
\mathrm{MOSHE}=
\frac{(\Gamma_{0-3\,km}-4)^2}{4}
\frac{S_{0-1.5\,km,m/s}-8}{10}
\frac{S_{eff,m/s}-8}{10}
\frac{\mathrm{MAXTEVV}+10}{9}.
```

`MAXTEVV` is not a generic maximum vertical velocity. The code examines 2-km-deep layers whose tops run from 2 to 6 km AGL in 500-m steps and takes the largest value of

```math
\frac{\theta_{e,bottom}-\theta_{e,top}}{2\,\mathrm{km}}
\left(-\omega_{top}\right).
```

This calculation requires an omega profile.

### Nontornadic Supercell Tornado Parameter (NSTP)

```math
\mathrm{NSTP}=\frac{\Gamma_{0-1\,km}}{9}
\frac{\mathrm{MLCAPE}_{0-3\,km}}{100}
\frac{225-\mathrm{MLCIN}}{200}
\frac{18-S_{0-6\,km,m/s}}{5}
\frac{\zeta_{sfc}}{8\times10^{-5}\,s^{-1}}.
```

Surface-relative vorticity is read from optional source metadata. Metadata values with absolute magnitude at least 0.01 are interpreted as units of $10^{-5}$ per second. If no supported metadata field is present, NSTP is missing.

### Normalized CAPE and normalized CIN

```math
\mathrm{NCAPE}=\frac{\mathrm{MUCAPE}}{z_{EL}-z_{LFC}},
```

```math
\mathrm{NCIN}=\frac{\mathrm{MUCIN}}{z_{LFC}-z_{MU,start}}.
```

Both are reported in J/kg/m. NCIN remains negative.

### Entraining CAPE (ECAPE)

The displayed ECAPE calculation uses the Peters-style analytic expression:

```math
a=\frac{\psi}{V_{SR}^{2}},
```

```math
\mathrm{ECAPE}=\frac{V_{SR}^{2}}{2}
+\frac{-1-\psi-2aN}{4a}
+\frac{\sqrt{(1+\psi+2aN)^2+8a(\mathrm{CAPE}-\psi N)}}{4a}.
```

Here $N$ is the moist-static-energy dilution integral computed through MetPy, in J/kg. It is **not** the displayed NCAPE value above. $V_{SR}$ is the mean 0–1-km storm-relative speed using MetPy Bunkers motion. The code computes

```math
\psi=\frac{k^2\alpha^2\pi^2L_{mix}}
{4\,Pr\,\sigma^2z_{EL,MSL}},
```

with $k^2=0.18$, $\alpha=0.8$, $L_{mix}=120$ m, $Pr=1/3$, and $\sigma=1.6$. The result is clamped to the range from zero to the undiluted SHARPpy MUCAPE. It is zero when MUCAPE is zero and missing when required inputs cannot be resolved.

### Left-moving Supercell Composite (LSCP)

```math
\mathrm{LSCP}=\frac{\mathrm{MUCAPE}}{1000}
\frac{\mathrm{left\ ESRH}}{50}
\frac{\mathrm{EBWD}_{m/s}}{20}F_{CIN}.
```

The EBWD term is zero below 10 m/s and capped at 20 m/s. $F_{CIN}=1$ when MUCIN is greater than -40 J/kg; otherwise $F_{CIN}=-40/\mathrm{MUCIN}$. The routine returns zero when no effective layer exists.

### Wet-bulb-zero height

This is the first AGL height where the interpolated wet-bulb temperature crosses 0 degrees Celsius:

```math
T_w(z_{WBZ})=0^\circ\mathrm C.
```

## Effective inflow layer

The upstream search first requires a usable most-unstable parcel. Starting at the surface, it tests parcels at successive observed levels against

```math
\mathrm{CAPE}\ge100\ \mathrm{J/kg}
\quad\text{and}\quad
\mathrm{CIN}>-250\ \mathrm{J/kg}.
```

The first passing level is the effective-layer base. The search continues upward until a level fails either condition; the top is the **last passing level**, not the first failing level. The layer supports ESRH, EBWD, effective parcels, and the effective-layer composites described above.

## Active display colors

Colors are presentation cues, not additional scientific thresholds. Missing values are neutral. The rules below describe the active draw-time code rather than stale threshold metadata.

### Main parcel table

| Field | Active color rule |
| --- | --- |
| CAPE | White below 1000; yellow at 1000; red at 2500; pink at 4000 J/kg. Requires positive CAPE. |
| CIN | Green at -50 J/kg or greater; orange from -100 to less than -50; red below -100. |
| LCL | Neutral white; the available generic LCL helper is not called by this table. |
| LFC / EL / MPL | Neutral white heights; missing levels display as `--`. |
| LI | White above -4; yellow at -4 or lower; red at -7 or lower; pink at -10 or lower. Requires positive CAPE. |
| 3CAPE / 6CAPE | Green above 25; yellow above 50; orange above 75; red above 100; magenta above 125 J/kg. |

The generic `cinh_color` helper has a separate CAPE-context-dependent alert scale, but it is not the scale used by the main parcel table. A parameter-board CIN call without that CAPE context remains neutral.

### Lapse rates and SWEAT

| Parameter | Color rule |
| --- | --- |
| Lapse rate | Green through 6; yellow above 6 through 7; orange above 7 through 8; red above 8 through 9; magenta above 9 degrees Celsius per km. Zero/missing is neutral. |
| SWEAT | Zero neutral; below 250 blue; 250–349 neutral; 350–499 yellow; 500–649 red; 650 or greater pink. |

### Severe-composite helpers

| Parameter | Amber | Yellow | Red | Pink | Other |
| --- | ---: | ---: | ---: | ---: | --- |
| SCP | 0 to less than 1 | 1 to less than 2 | 2 to less than 5 | 5 or greater | Negative values cyan |
| STP(cin) | 0 to less than 1 | 1 to less than 2 | 2 to less than 5 | 5 or greater | Negative values neutral |
| STP(fix) | 0 to less than 1 | 1 to less than 2 | 2 to less than 5 | 5 or greater | Negative values neutral |
| SHIP | 0 to less than 1 | 1 to less than 2 | 2 to less than 3 | 3 or greater | — |
| DCP | 0 to less than 1 | 1 to less than 4 | 4 to less than 6 | 6 or greater | — |
| LRGHAIL | — | 4 to less than 7 | 7 to less than 10 | 10 or greater | Below 4 neutral |

The palette constants behind these tiers are amber `#c8911f`, yellow `#ffff00`, red `#ff0000`, and pink `#ff00ff`. Negative SCP is cyan.

### Other white-yellow-red-pink gradients

| Parameter | Yellow at | Red at | Pink at |
| --- | ---: | ---: | ---: |
| EHI | 1 | 2 | 3 |
| MCS logit | 1 | 2 | 3 |
| NSTP | 1 | 2 | 4 |
| Modified SHERBE | 1 | 2 | 3 |
| Peskov | 1 | 4 | 7 |
| HGZ CAPE | 1000 | 2500 | 4000 |
| NCAPE | 0.1 | 0.2 | 0.3 |
| ECAPE | 1000 | 2500 | 4000 |

LSCP uses the inverse negative scale: yellow at -1 or lower, red at -4 or lower, and pink at -8 or lower; zero and positive values are neutral.

## Maintenance source map

When code and this guide disagree, inspect these locations before changing the prose:

| Subject | Authoritative implementation |
| --- | --- |
| Standard parcels, CAPE/CIN, LCL/LFC/EL, PWAT, DCAPE, effective layer, standard composites | Installed `sharppy.sharptab.params` and `sharppy.sharptab.profile` |
| Custom thermodynamic and severe parameters | `sharpmod/sharptab/derived.py`, `params.py`, and `ecape.py` |
| Wind means, shear, SRH | `sharpmod/sharptab/winds.py` |
| Profile wiring and exposed custom attributes | `sharpmod/sharptab/profile.py` |
| Main GUI values and parcel-table colors | `sharpmod/viz/index_board.py` |
| Custom parameter-board values | `sharpmod/viz/param_board.py` |
| Streamwiseness | `sharpmod/viz/streamwiseness.py` |
| Color dispatch | `sharpmod/colors.py` plus the calling widget |

This split matters: a generic helper, a dormant metadata table, and a value actually painted by a widget can legitimately differ. The user-visible draw path is the final authority for this guide's color section.
