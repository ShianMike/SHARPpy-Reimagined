# Worldwide weather radar inventory

Research checked 9 November 2026. Provider offerings, terms, availability and
quotas can change. A publicly viewable picture or a radar's technical capability
does not establish permission to redistribute its numerical measurements. Unless
identified as implemented below, these are research findings, not app features.
Unknown access, station, product, timing or licence details must be verified
before integration.

## Implemented: Germany DWD DX

The app has 17 DWD stations in addition to the existing US NEXRAD and Australian
MRRL sites. The German station IDs use `D` plus the source's three-letter code,
for example `DESS` for Essen. Names, WMO identifiers and WGS84 coordinates are
in [`german_radar_sites.json`](../rust/weather-core/resources/german_radar_sites.json),
transcribed from DWD's [site directory](https://opendata.dwd.de/weather/radar/sites/dx/)
and [official coordinates](https://www.dwd.de/DE/derdwd/messnetz/atmosphaerenbeobachtung/_functions/Teasergroup/Koordinaten_Radarstandorte_Windprofiler.pdf?__blob=publicationFile&v=3).
On the research date, all 17 site directories contained filenames matching their
bundled WMO identifiers. This verifies the listings, not continuous operation.
The list is bundled rather than automatically refreshed; a listed station may
be offline. DWD geodata is publicly reusable with [source attribution](https://www.wettergefahren.de/copyright.html),
and DWD [specifies placement and change notices](https://www.wettergefahren.de/vorlagen_quellenangabe.html).
The map and metadata credit DWD and identify the data as rendered raster data.

Only the single-site **DX precipitation-scan reflectivity** product is
implemented. DX is a numerical polar scan, not a full volume or a national
composite. DWD describes 1 km × 1° sampling and five-minute updates in its
[radar-product overview](https://www.dwd.de/DE/leistungen/radolan/radarniederschlagsprodukte/radar_produkt%C3%BCbersicht.pdf?__blob=publicationFile&v=2).
Each ray's actual elevation is used for georeferencing; this terrain-following
scan has no single selectable tilt. The timestamp is the source's nominal scan
time, not a per-ray timestamp. Values use the RVP6 mapping `code / 2 - 32.5`
dBZ, as corroborated by [wradlib's DX reader](https://github.com/wradlib/wradlib/blob/main/wradlib/io/radolan.py).
Clutter-flagged gates are masked. Values below 0 dBZ remain in the numerical PNG
lookup but are transparent on the map. Missing scans and requests over ten
minutes away from a published scan are reported unavailable. The timeline uses
up to one hour of available history; DWD's directory contains more data but the
app does not promise an archive. Directory and frame caches are bounded, and
requests to DWD are limited to three concurrent operations.

DWD also publishes [HDF5 volume and precipitation sweeps](https://opendata.dwd.de/weather/radar/sites/)
for reflectivity, radial velocity and polarimetric quantities. These are **not**
decoded by this implementation; a listing is not proof every site, sweep or
quantity is available at every time. Do not label DX as Doppler velocity, a
dual-polarization product, or a selectable volume cut.

### Validation and limits

The checked-in [Essen sample](../rust/weather-core/resources/dwd_ess_20261109_0255.bin)
was downloaded from
`https://opendata.dwd.de/weather/radar/sites/dx/ess/raa00-dx_10410-2611090255-ess---bin`.
Its SHA-256 is `15a23a326972f25eb8cc54798e01847d586fb36425a1e83a400a635575c58b48`.
It contains 360 rays and 128 gates per ray. Its first ray is 0° azimuth and
0.7° elevation; the first encoded word `0x0043` decodes to 1 dBZ, while `0x1002`
is a run of two zero codes, each mapping to -32.5 dBZ. Rust tests check those
values, per-ray elevation, time and station matching, invalid/truncated inputs,
clutter masking and the image's numeric lookup. This is a comparison with the
published encoding and source bytes, not an independent gauge or instrument
calibration. The renderer preserves up to 65,534 distinct finite gate values;
scans with more than 254 use a compressed 16-bit lookup for the exact readout.

A live DWD Essen scan and an Australian MRRL scan were downloaded and rendered;
the local Docker web preview showed live German radar, selection, attribution
and scan controls. A Browser capture is saved locally at
`.test-results/worldwide-radar/browser-germany.jpg`. This does not guarantee
service availability or validate every site, extreme range, terrain condition,
historical time or desktop build.

## Implemented: compatible EUMETNET OPERA scans

The Europe network discovers the [OPERA ORD catalogue](https://eumetnet.github.io/openradardata-documentation/2-ORD-API-discovering-and-accessing-data/)
at runtime. On 9 November the catalogue supplied 128 non-German sites advertising
DBZH; this is a count of catalogue entries, not a count of working or compatible
radars. Germany's entries are omitted from this network because the DWD sites
already represent those antennas. Discovery is cached for 15 minutes and a
previous successful catalogue is reused if a refresh fails. If the service is
unavailable on startup, the bundled US, Australian and German sites still work.

Live Tracking displays radar codes, such as `KTLX` for NEXRAD and `NOHUR` for
the OPERA site `0-578-0-nohur`. Place names remain searchable. The OPERA
catalogue supplies place names, with the bracketed node code removed from the
name. For records that carry only the node code, the app bundles 30 verified
fallback names matched by ODIM code and coordinates from the
[EUMETNET OPERA station registry](https://github.com/EUMETNET/openradardata-validator/blob/main/src/openradardata_validator/stations/OPERA_RADARS.csv).
Selected local spellings come from [MeteoSwiss](https://www.meteoswiss.admin.ch/dam/jcr%3Abb6c4226-fd02-48df-a63b-cd2485eb1f72/technical-report-284.pdf),
[ČHMÚ](https://www.chmi.cz/namerena-data/radar-nowcast/vice-o-radarech),
[MET Norway](https://www.met.no/nyhetsarkiv/ny-vaerradar-i-finnmark-gir-bedre-varsling),
[Meteo Romania](https://www.meteoromania.ro/despre-noi/meteorologie-operationala/activitati-de-baza/radar/reteaua-nationala-radar/)
and [SHMÚ](https://www.shmu.sk/sk/?page=1566). The 155 bundled NEXRAD sites
use names from [NWS station metadata](https://api.weather.gov/radar/stations)
and [NOAA NCEI's station list](https://www.ncei.noaa.gov/access/homr/reports).
Scan requests and saved selections still use the full station IDs.

The Rust provider reads **DBZH** in ODIM 2.0–2.42 PVOL or SCAN files across
8-bit and 16-bit signed/unsigned integer and 32-bit/64-bit floating-point
datasets, showing the lowest recent available scan with measured gain/offset,
nodata and undetect codes, source azimuths where supplied (including ODIM 2.0
`azangles` strings), version-specific range origins (`rstart` in km for
ODIM 2.0–2.3 and metres for 2.4+), and scan end time. Rays use source
elevations where supplied; otherwise the sweep elevation is used. Below 0 dBZ
is transparent, but remains in the image's numeric lookup. Sweeps with more
than 254 distinct finite values store a compressed 16-bit gate lookup in the
PNG's `zTXt` `sharppy-radar` chunk so hover readouts retain exact unquantized
values while the map displays an 8-bit indexed colour ramp. Only reflectivity
is offered; velocity, individual tilt selection, other data types and
composites are not implemented. The app reports incompatible files and missing
scans as unavailable rather than substituting another measurement.

Item licences are checked before downloading: CC BY 4.0 and, for French sites,
the published Licence Ouverte 2.0 are accepted with separate visible credits.
Other or missing licences are not assumed. Downloads are limited to trusted
OPERA storage links and 32 MiB per file, with bounded HDF5 reads, 12 cached
frames, 75-minute history listing reuse and at most three simultaneous
requests. The app searches a recent window and rejects sweeps more than ten
minutes before the requested time. Listed stations can still be offline, use an
unsupported licence, or publish files outside these bounds; the catalogue must
not be read as a guaranteed coverage map.

The test fixtures are real EUMETNET OPERA data made available under CC BY 4.0:
Norway's MET Norway [Hurum volume](../rust/weather-core/resources/opera_nohur_20261109_0330.h5)
(`66315fc61d396bd97104520ac85ea15c5c93488ef8875e9ed821c58e31894576`),
and Finland's FMI [Anjalankoski scan](../rust/weather-core/resources/opera_fianj_20261109_0345.h5)
(`46039666a142d6e63a55974c449657a7ce54e2b436ff66618b9e515b98d47883`).
Both were retrieved from the 24-hour OPERA cache on 9 November 2026; files are
unchanged, and the app renders derived images. Tests compare the Norwegian
sample's encoded values and geometry with its source metadata, check Finland's
explicit azimuth arrays and DBZH field selection, verify compressed 16-bit gate
lookups for high-precision sweeps, and reject wrong stations and future times.
Live requests rendered samples across all 20 non-German European countries in
the current OPERA catalogue, including 16-bit Swedish and Dutch scans,
floating-point Spanish scans, and ODIM 2.0/2.1/2.3/2.4 volumes from Belgium,
Croatia, Czechia, Denmark, Estonia, Finland, France, Hungary, Iceland, Ireland,
Lithuania, Malta, Norway, Poland, Romania, Slovakia and Switzerland. A local
capture is saved at
`.test-results/worldwide-radar/browser-opera.jpg`. These checks do not validate
every national encoding or prove instrument calibration.

## MRRL InterRAD catalogue and polling service

Checked 11 November 2026. MRRL publishes a [worldwide station list](https://feeds.mrrl.net/mrrlworldwide_customradars.gis.txt)
and a separate [polling service](https://feeds.mrrl.net/polling/) containing
individual converted Archive II volumes. The station list is useful for names,
identifiers and coordinates; an entry does not by itself establish that a current
volume or every radar product is available. It includes sites already represented
by the Australian, DWD and OPERA sources as well as sites in other regions.
Some identifiers and coordinates are aliases, and mobile radars may move, so
entries need to be matched before adding them to the map.

MRRL's [information file](https://feeds.mrrl.net/INFORMATION.txt) describes
differences in national scan patterns, delayed Japanese data, low Nyquist
velocities and cases where an earlier velocity scan is combined with a newer
surveillance scan. It also describes quality control applied to some feeds and
asks application developers preferably to contact the team about using the feed.
The [attribution file](https://feeds.mrrl.net/ATTRIBUTION.txt) identifies the
upstream sources and says French data, including overseas territories, uses
Licence Ouverte 2.0. Météo-France describes its public [radar API](https://www.data.gouv.fr/dataservices/api-donnees-radar)
as requiring an account and offering station and overseas data at five-minute
frequency; its [radar package dataset](https://dev.data.gouv.fr/datasets/package-radar)
lists Licence Ouverte 2.0. A converted volume is not evidence that every original
moment, scan time or range-folding flag survived the conversion unchanged.

### Implemented French sites via MRRL

The app includes eleven MRRL sites from Météo-France: Noyal, Saint-Rémy,
Moucherotte, Maurel and Colombis in metropolitan France; Le Moule in Guadeloupe;
Martinique; Colorado and Piton Villers in Réunion; and Nouméa and Lifou in New
Caledonia. Their names, coordinates and MRRL IDs are bundled from the catalogue.
They are additional to the French antennas already represented by OPERA. When
a future OPERA catalogue entry has a matching country and name within 500
metres, the app keeps the OPERA entry; OPERA's France prefix also matches these
French territories. This rule is a heuristic, not a complete registry of
antenna aliases.

These sites offer only the lowest available converted reflectivity sweep, in
dBZ; the app does not expose velocity or selectable tilts for them. Source gate
spacing and the decoded sweep angle are used for rendering, with a 4/3-Earth
beam approximation. Returns below 0 dBZ remain in the numerical lookup but are
transparent on the map. The displayed time comes from the converted file. In a
matched Ajaccio sample, the MRRL radials all had the OPERA sweep's end time, so
this must not be presented as independently measured per-ray timing. The MRRL
and OPERA samples also have different grids; the comparison does not establish
that their reflectivity fields are identical.

The offline [Nouméa sample](../rust/weather-core/resources/mrrl_mf94_20261111_100933.ar2v)
is an MRRL-converted Météo-France volume, credited under Licence Ouverte 2.0;
its SHA-256 is `79d87941373f12f1b1ce25ee86407e67d362819ebe091f7c23349e8a75afdccc`.
Tests check decoding, source/site matching, the reported time, numeric lookup,
transparency and unsupported products. A successful sample does not establish
continuous availability or validate all geometry and values for every station.

Access and reuse must be checked for each source. In particular, NICT describes
its [JMA polar radar redistribution](https://www2.nict.go.jp/res/JMA-PolarCoordsRadar/)
as provided for research and educational institutions and directs frequent
commercial users to obtain the data through the meteorological business support
centre. Environment and Climate Change Canada lists [raw radar volume feeds as
cost-recovered](https://eccc-msc.github.io/open-data/cost-recovered/readme_en/),
while MRRL identifies an open government licence for its Canadian feed; the
terms governing this downstream conversion are not established by that comparison.
Mexico's [SMN terms](https://smn.conagua.gob.mx/es/terminos-y-condiciones)
require source credit and describe conditions on commercial reuse; their exact
application to the MRRL conversion also needs confirmation. A catalogue listing
alone does not establish that a site is implemented or continuously available.

## Ranked opportunities

The order weighs geographic reach, scientific content, practical access,
ongoing limits and the work needed to preserve units and geometry. Reliability
is not inferred from a single successful request. Free visual imagery is not a
substitute for an independently calibrated numerical radar field.

| Priority | Source and coverage | Available data and access | Decision |
| --- | --- | --- | --- |
| 1 | [EUMETNET OPERA ORD](https://eumetnet.github.io/openradardata-documentation/2-ORD-API-discovering-and-accessing-data/) across participating European countries | Recent individual-site ODIM HDF5 volumes; older BUFR; 24-hour rolling cache. DBZH, TH and VRADH are common, but vary by item; dual-pol exchange is described as planned. Composites include maximum reflectivity, rain rate and accumulation in HDF5 or COG. Anonymous access works; inspect rate headers and each item's licence, normally CC BY 4.0 with exceptions. | Compatible 8-bit, 16-bit and floating-point DBZH scans across ODIM 2.0–2.42 implemented as described above. Radial velocities, full sweep selection, composite data and broader historical validation remain opportunities. |
| 2 | [DMI Denmark](https://www.dmi.dk/friedata/dokumentation/radar-data-api) | Five named radars at Stevns, Rømø, Sindal, Bornholm and Samsø; volume, pseudo-CAPPI and composite HDF5. [No key or registration](https://www.dmi.dk/friedata/dokumentation/authentication) since December 2025. [CC BY 4.0 and fair use](https://www.dmi.dk/friedata/dokumentation/terms-of-use); avoid background polling on inactive mobile clients. Volume endpoint distinguishes 240 km full-range and 120 km Doppler scans. | Good ODIM test network after decoder validation; check available quantities, exact cadence and actual station metadata per response. DMI's FAQ also says retrieved data may not be changed, while its licence permits adaptation: resolve the apparent wording conflict before using transformed data. |
| 3 | [DWD Germany](https://opendata.dwd.de/weather/radar/sites/) | Open individual-site DX plus separate HDF5 reflectivity, velocity and polarimetric sweeps. Attribution required; see above. | DX implemented. HDF5 quantities are a high-value follow-up after ODIM tests, with no implication they are already supported. |
| 4 | [Canada ECCC / MSC](https://eccc-msc.github.io/open-data/faq/readme_en/) | Free North American composite via GeoMet WMS, and GIFs; the [WMS example](https://eccc-msc.github.io/open-data/usage/tutorial_web-maps_en/) describes six-minute updates and three-hour history. Provider also mentions GeoTIFF; examine the actual dataset and encoding before claiming numerical values. Raw ODIM H5 and several other feeds are [cost recovered](https://eccc-msc.github.io/open-data/cost-recovered/readme_en/); archive retrieval is separately charged. | Attractive North American composite, but WMS imagery is not a single-site Doppler scan. Review open licence/attribution, projection, legend and numerical GeoTIFF before choosing an overlay or scientific grid. |
| 5 | [Italy Radar-DPC](https://mappe.protezionecivile.gov.it/en/risks-maps-and-dashboards/radar-map/) | National VMI and surface-rainfall-intensity composites update every five minutes; gauge-adjusted accumulations hourly; history up to 14 days. Platform provides REST documentation and station status. CC BY-SA with source credit and share-alike for derivatives; coverage may be reduced during upgrades. | Useful composite; confirm the current API, format, exact reuse obligations and map reprojection. Not evidence of open individual Doppler volumes. |
| 6 | [Netherlands KNMI](https://developer.dataplatform.knmi.nl/open-data-api) | File API requires an Authorization key. A published, expiring shared anonymous key exists; registered users get a separate key. Documented quotas include 50/minute and 3,000/hour shared anonymous, or 1,000/hour registered; excessive polling is prohibited. | Verify the precise full-volume dataset, terms, stations and cadence; use notifications or restrained polling. Avoid hard-coding an expiring key. |
| 7 | [Singapore NEA](https://data.gov.sg/datasets?formats=API&resultId=d_c49410cc1e293b0a7213a433ab612067&sort=updatedAt) | Public beta radar-image endpoints for 70, 240 and 480 km. The 240 km endpoint returned an expiring PNG URL and geospatial metadata anonymously in this check. | Image-only regional option. Verify terms, quotas, cadence and reprojection from the supplied azimuthal-equidistant grid; do not invent dBZ or velocity from colours. |
| 8 | [South Korea KMA](https://data.kma.go.kr/data/rmt/rmtList.do?code=10&pgmNo=61&tabNo=2) | The agency lists individual raw/quality-controlled radar data in NetCDF and images, plus historical UF; its composite listing describes recent five-minute products. Download and account conditions vary. | Investigate exact live access, permitted redistribution, locations and field metadata before implementation. |
| 9 | [Taiwan CWA](https://opendata.cwa.gov.tw/dataset/observation/O-A0058-001) | A free 10-minute composite PNG; the portal lists source radars and requires login for the download workflow. [Platform rules](https://opendata.cwa.gov.tw/about/rules) apply. | Visual composite only until numeric data, projection, terms and a documented access method are independently verified. |
| 10 | [France Météo-France](https://www.data.gouv.fr/dataservices/api-donnees-radar) | Its public API describes station and metropolitan/overseas mosaic products at five-minute frequency; an account is required and the stated limit is 850 requests per five minutes. The [radar package dataset](https://dev.data.gouv.fr/datasets/package-radar) lists Licence Ouverte 2.0. MRRL attributes its converted French feeds to Météo-France under that licence. | Confirm the available quantity, geometry and time in each converted feed; the public API description does not establish that every site has an open full Doppler volume. |
| 11 | [Finland FMI](https://en.ilmatieteenlaitos.fi/open-data-manual-fmi-wms-services), [Norway MET](https://api.met.no/weatherapi/radar/2.0/documentation), [Sweden SMHI](https://www.smhi.se/en/weather/radar-and-satellite/radar-and-lightning) | Nordic visual/composite services and open-data routes exist. FMI's WMS is intended for evaluation and must not be embedded as a production source; MET documents radar images. | Prefer permitted numerical national or OPERA products. Verify Finnish WFS/download terms and each agency's station, time, quota and attribution metadata. |
| 12 | [Spain AEMET](https://datos.gob.es/ca/blog/los-conjuntos-de-datos-meteorologicos-de-alto-valor) | Regional and national radar reflectivity images are documented; AEMET OpenData uses an API key. Other radar variables were not established by that source. | Check current API terms, cadence, extent and licences; treat documented images as visual products. |
| 13 | [Japan JMA](https://www.data.jma.go.jp/suishin/cgi-bin/catalogue/make_product_page.cgi?id=Nowcast) | Precipitation nowcasts are gridded derived/forecast products, including GRIB2 at five-minute cadence; the catalogue identifies dissemination channels. | Useful separate precipitation product, not a raw polar radar. Check real-time access and rights. |
| 14 | [New Zealand MetService](https://about.metservice.com/our-data-access-policy) | Open-access observational data includes designated radar imagery; the [access page](https://about.metservice.com/open-access-data) describes request-based delivery. | Verify the exact data and SFTP conditions, cadence and reuse before integration. |
| 15 | India [IMD](https://mausam.imd.gov.in/hyderabad/mcdata/radarprd.html), Indonesia [BMKG](https://www.bmkg.go.id/cuaca/radar), Philippines [PAGASA](https://pagasa.dost.gov.ph/products-and-services/engineering-services) | Agency pages show imagery or describe radar equipment. IMD shows reflectivity and velocity images, but its internal data flow does not itself grant public access to raw files. | Public automated access, station coordinates, history, licence, limits and numerical products remain unverified. |
| 16 | [Brazil INMET](https://portal.inmet.gov.br/produtos/radar) and [South African installation inventory](https://portal.environment.gov.za/server/rest/services/ScreeningMaps/SolarPV_Original_Data/MapServer/65) | Public radar visualizations or station inventories exist; an installation list is not a data feed or a reuse licence. | Research authoritative data routes, neighbouring countries, access, rates, formats and rights. Do not infer scientific coverage. |
| 17 | [RainViewer](https://www.rainviewer.com/api.html) | Image tiles, not measured polar values. The [transition notice](https://www.rainviewer.com/api/transition-faq.html) lists personal/educational access, two hours in 10-minute steps, zoom 7 and 100 requests/IP/minute; attribution and other restrictions apply. | Optional visualization-only fallback after current terms and application eligibility are confirmed. No velocity or exact dBZ lookup. |

## OPERA discovery snapshot and integration requirements

An anonymous request to the [locations endpoint](https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/locations?f=GeoJSON)
returned 146 features: 145 individual radar metadata entries plus a composite.
[`radar-opera-stations-2026-11-09.csv`](radar-opera-stations-2026-11-09.csv)
records WIGOS identifiers, advertised names, coordinates and parameter labels.
It is a dated discovery snapshot, not a claim that all 145 are operating,
licensed for every use, or supplying each advertised quantity. For example,
Hurum, Norway (`0-578-0-nohur`) returned recent volume links with per-item
licence metadata during this check. Per-item availability and units must be
read from and checked against the actual file; one response advertised
inconsistent unit metadata for DBZH.

The implementation uses pure-Rust `hdf5-reader` 0.12.1, which declares Rust
1.81 and supports bounded reads; the project's minimum is 1.88. The alternative
`hdf5-no-c` 0.1.1 failed on the Hurum sample's 4-byte HDF5 offsets. These are
sample-specific results, not a comprehensive comparison. Sweeps with more than
254 distinct finite values store a compressed 16-bit gate lookup alongside the
8-bit visual colour table so exact values are preserved without quantization.
Future work should handle per-ray times, radial velocity, selectable tilts, and
authoritative WIGOS/ODIM aliases for stations shared by additional providers.
