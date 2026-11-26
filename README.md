# usaciv: the United States as a strategy game, built from real data

**Live: [karpathy.ai/usaciv](https://karpathy.ai/usaciv)** · Source code:
[usaciv-source.zip](https://karpathy.ai/usaciv/usaciv-source.zip) (also linked from the in-app help, `?`)

![The lower 48 drawn as a 4X strategy map: painted hex terrain, rivers, state names and bronze city banners](docs/images/hero.jpg)

A visualizer (not a game) that draws the **real** United States like a turn-based strategy map. Hex terrain comes
from elevation and land-cover data. Cities have yield banners built from GDP and job counts. Resources are real
power plants, mines and shale plays. Roads, railroads, ports, forts and wonders sit on the tiles. There are lenses,
a City View with citizens and districts, and a council of Advisors for slicing the data. Every number traces back
to a public source in the in-app **Almanac**.

It is a static site: plain ES modules and Canvas 2D, with no framework, no npm and no build step. The data is built
by a small Python pipeline that uses only the standard library, from public downloads that need no API keys.

<table>
  <tr>
    <td><img src="docs/images/east-coast.jpg" alt="The Northeast at mid zoom: Interstates and railroads between city tiles, ports, universities and the city banners of New York, Philadelphia, Baltimore and Washington"></td>
    <td><img src="docs/images/california.jpg" alt="Central California up close: the snowy Sierra, Central Valley farmland and the coast from San Luis Obispo to Los Angeles"></td>
  </tr>
  <tr>
    <td><img src="docs/images/rockies.jpg" alt="The Rockies and the Colorado Plateau: snow-capped ranges, mesas, the Great Basin, Denver and Salt Lake City"></td>
    <td><img src="docs/images/lens-income.jpg" alt="The income lens: every county coloured by median household income"></td>
  </tr>
</table>

## What's in it

- **The map.** Painted hex terrain: forests, crop patchwork, wooded hills, snowy ranges, Great Basin ranges and
  playas, Colorado Plateau mesas, marsh and suburbs. Coasts and lakes are smoothed. There are rivers and state
  colours. Cities grow from villages into skylines across several hexes. All art is drawn in code.
- **Yields.** The top bar and the city banners show Food, Production, Gold, Science and Culture. Gold is GDP from BEA.
  The others are job counts by industry from the ACS. Click a yield for a report with leaderboards.
- **Tile improvements.** Interstates and major highways, railroads, the principal ports (ranked by tonnage) and the
  busiest airports. Turn them on and off from the **Map** menu.
- **Military.** DoD installations are drawn as forts, one glyph per service, and the largest ranges as restricted
  ground. The Military advisor has troops and defense dollars by state.
- **Wonders.** 52 wonders, each chosen by a published rule: the most-visited national parks, the R1 universities
  with the most R&D, and a set of landmarks.
- **Trade routes.** The largest interstate freight flows between metro areas (FAF5) are drawn as caravan arcs.
- **Lenses.** Colour every county by income, degrees, poverty, growth, broadband, homeownership, land cover, faith,
  wellbeing, disasters, veterans and more. The legend is a histogram you can brush, and it states the caveats.
- **City View.** Click a city:
  - Its **citizens** appear as rows of little figures by occupation, age, commute, education and origin, so
    San Jose's row looks different from Elkhart's.
  - Its **districts** come from real industry specialisation (BLS QCEW location quotients): Campus, Commercial hub,
    Industrial zone, Harbor, Theater square, Aerodrome and others. It gets a city-type badge such as
    "Tech city" or "Motor city".
  - It also shows GDP by industry, the workforce, resource sites, peer cities and the metro's hexes on the map.
- **Advisors.** Docked dashboards for Energy & Resources, Economy, People, Prosperity & Housing, Knowledge, Land,
  Military, Commerce and Culture, each linked both ways with the map. The **Explorer** turns any column of
  states, metros or 3,100+ counties into a table and a scatter plot, and highlights your selection on the map.
- **Sharing and navigation.**
  - Every view has a link (the URL hash holds it), and share images are rendered off-screen.
  - Search with `/`; press `?` for keyboard help.
  - Works on phones.
- **Art gallery** at `/dev/art.html`: every terrain, city, water, icon, banner and map-layer specimen at four zoom
  levels, for working on the art.
- **Trailer maker** at `/dev/trailer.html`: renders a 30-second 1080p MP4 trailer of the map frame by frame,
  entirely in the browser (WebCodecs H.264 and AAC, and a small hand-written MP4 writer, with no tools to install).
  Its soundtrack is an original score synthesised in code with the Web Audio API and timed to the cuts
  (`web/dev/trailer/score.js`). The shots, captions, title and URL are in `web/dev/trailer/storyboard.js`.

## Quick start

You need [uv](https://docs.astral.sh/uv/), which fetches a suitable Python for you (3.11+). Nothing else needs
installing.

```bash
uv run python -m pipeline serve        # http://127.0.0.1:8765
```

The built data (`web/data/`, about 6 MB) is included, so that is all it takes to run it. `web/` is a self-contained
static site that you can upload to any static host (Netlify, S3, GitHub Pages…).

To make a deploy package:

```bash
uv run python -m pipeline package      # → usaciv.zip: one file to upload
```

`usaciv.zip` unzips to a `usaciv/` folder: `web/` without the dev pages, plus `usaciv-source.zip`: this whole project, minus downloads and
caches, which the in-app help links to. If you publish your own copy, change the address `karpathy.ai/usaciv` in
`web/index.html` (the social-card tags), in `SITE_URL` in `web/js/app/share.js` (printed on share images) and in
`web/dev/trailer/storyboard.js`.

To rebuild the data from the original publishers:

```bash
uv run python -m pipeline all          # download (cached) → process → build web/data/*.json → check
```

The first full run downloads about 500 MB into `data/raw/` (not included in the source zip). Later runs reuse the cache.

## How it works

```
pipeline/
  core/       shared plumbing: cached downloads with a provenance manifest (fetch.py), the Source base class,
              projection (geo.py), hex grid (hexgrid.py), atomic JSON IO (store.py)
  sources/    one module per data source: fetch() raw files, process() → data/interim/<id>.json
  build/      builders that put the sources onto the hex map → web/data/*.json
  check.py    validates web/data against docs/DATA_CONTRACTS.md and prints a data-quality report
tests/        unittest suite (standard library only)
web/
  js/         main.js (app shell), camera, hex grid (mirrors pipeline/core/hexgrid.py),
              render/ (map layers, banners, off-screen snapshots) and render/art/ (all the tile art),
              ui/ (panels, lenses, Almanac, minimap), app/ (app API, search, sharing, URL state),
              ext/ (extensions: City View, Advisors, map layers)
  data/       the generated JSON the browser loads
  dev/        the art gallery
docs/         DATA_CONTRACTS.md: the shape of every web/data file, shared by pipeline and browser
```

The flow is always **source → interim → builder → web/data**:

- **Sources** know nothing about hexes or the UI. Each one turns a publisher's files into tidy JSON. Any `Source`
  subclass with a `SourceInfo` is discovered automatically.
- **Builders** put that data onto the shared 25 km hex grid (an Albers equal-area projection). They declare
  `REQUIRES` and `PRODUCES`, run in dependency order and are skipped when they are up to date. A builder may define
  `check(web_dir)` to add its own lines to the data-quality report.
- **`catalog`** writes `sources.json` from the source registry and the download manifests (URL, sha256, size and
  date of every file). That is the Almanac's bibliography, so provenance stays accurate automatically.
- **`check`** is the gate: every line is PASS, NOTE, WARN or FAIL, and any FAIL exits 1.

The browser side has a small app API (`web/js/app/api.js`, documented in `docs/DATA_CONTRACTS.md`). The City View,
the Advisors and the map layers are all **extensions** built on it, and so can anything you add:
- `registerStaticLayer` draws into the cached map.
- `registerOverlay` draws per frame.
- `registerMode` and `ui.dock` give you a docked screen.
- `addMapToggle` adds a switch to the Map menu.
- `loadData` lazy-loads a data file.
- `setHighlight` links a view to the map.

### Commands

```bash
uv run python -m pipeline list                        # sources and builders (in build order)
uv run python -m pipeline fetch [source …]            # download raw files (cached; --force re-downloads)
uv run python -m pipeline process [source …]          # raw → data/interim/<source>.json
uv run python -m pipeline build [builder …]           # interim → web/data; up-to-date builders are skipped
uv run python -m pipeline build cities --deps         # …also run the builders `cities` depends on
uv run python -m pipeline build --force               # rebuild everything
uv run python -m pipeline check                       # validate web/data + data-quality report (exit 1 on FAIL)
uv run python -m pipeline all [--offline]             # fetch + process + build + check
uv run python -m pipeline serve [--port 8765]         # serve web/
uv run python -m pipeline package                     # usaciv.zip: the site to upload, with the source inside
uv run python -m unittest discover -s tests           # tests
```

`--offline` never touches the network: it uses the newest complete cached release of each source.

### Adding a data source

1. Create `pipeline/sources/<name>.py` with a `Source` subclass. Give it a `SourceInfo` (title, publisher,
   license, description, what it's used for), a `fetch()` that calls `self.download(url, filename)`, and a
   `process()` that returns a dict.
2. Use it from a builder: extend an existing one or add a module to `pipeline/build/` with `REQUIRES`, `PRODUCES`
   and `build()`. A new lens is just another entry in `lenses.json`. Document any new file shape in
   `docs/DATA_CONTRACTS.md`.
3. Run `uv run python -m pipeline fetch <name> && uv run python -m pipeline process <name> && uv run python -m pipeline build --deps <builder> && uv run python -m pipeline check`.

## Data sources

All are public and none needs an API key: 31 sources in all. The Almanac (open it from the top bar) lists the exact
files, URLs, checksums and caveats.

| Source | Publisher | Used for |
|---|---|---|
| Cartographic boundaries 2023, 1:20M | U.S. Census Bureau | Land vs. water, state borders, county lookup |
| Gazetteer 2024 | U.S. Census Bureau | City locations, county land area |
| CBSA delineation files, July 2023 | Census / OMB | Metro member counties, principal cities |
| American Community Survey 1-year (2025) | U.S. Census Bureau | City and state yields and panels, citizens, the national top bar |
| American Community Survey 5-year (2024) | U.S. Census Bureau | County lenses, veterans |
| Population estimates (vintage 2025) | U.S. Census Bureau | Population growth, yearly metro population |
| CAGDP2, GDP by county and industry | Bureau of Economic Analysis | Gold yield (GDP), GDP by industry |
| ETOPO1 via ERDDAP | NOAA NCEI | Relief, hill-shading, ocean depth |
| NLCD 2021 | USGS / MRLC | Land cover |
| Natural Earth 1:50m and 1:10m | Natural Earth | Neighbouring countries, lakes, rivers, roads, railroads |
| EIA-860M generator inventory | Energy Information Administration | Power plants |
| Mine-level coal production | Energy Information Administration | Coal mines |
| Mines data set | Mine Safety and Health Administration | Mine locations, metal mines |
| Tight oil & shale gas plays | Energy Information Administration | Oil and gas resources |
| QCEW annual averages by county | Bureau of Labor Statistics | Districts and city-type badges |
| Principal Ports of the United States | U.S. Army Corps of Engineers | Ports |
| Enplanements, all-cargo data, NASR | Federal Aviation Administration | Airports |
| Freight Analysis Framework FAF5 | FHWA / BTS | Trade routes |
| MIRTA installations and ranges, FY2025 | Department of Defense | Forts, restricted ground |
| Personnel by duty state | Defense Manpower Data Center | Troops by state |
| Defense Spending by State | DoD Office of Local Defense Community Cooperation | Defense dollars by state |
| Park boundaries; Visitor Use Statistics | National Park Service | Park wonders, protected land |
| National Historic Landmarks | National Park Service | Landmark wonders |
| IPEDS directory and enrollment | NCES, Department of Education | Universities |
| HERD Survey | National Science Foundation (NCSES) | Universities ranked by R&D |
| 2020 U.S. Religion Census | ASARB, distributed by ARDA | Faith lenses |
| PLACES county data | CDC | Wellbeing lens |
| Disaster Declarations Summaries | FEMA (OpenFEMA) | Disasters lens |
| Billion-Dollar Weather and Climate Disasters | NOAA NCEI | Billion-dollar disasters by state |

### Reading the map honestly

The map is drawn to be fun, but the numbers are real, so it is worth knowing where the game metaphor stops:

- **Yields are names given to real figures.** Gold is GDP. Food, Production, Science and Culture are job counts by
  industry, counted where people live.
- **Positions are schematic.** Each hex is about 1,600 km² and shows the place it covers. District plaques show
  what a metro specialises in, not where in the metro those jobs are. Roads and rail are generalised line work.
- **Missing data stays missing:** it is null, never 0. Where a publisher suppresses a figure, the map says so or
  marks the estimate (≈).
- **Selections are rules, not opinions.** Wonders, forts and trade routes are picked by rules that are published
  inside the data files and shown in the app.
- **Every lens states its caveat.** For example, the Religion Census counts congregations' adherents, not what
  residents believe, and covers about half the population.

### Credits and terms

The code is MIT-licensed (see [LICENSE](LICENSE)). That licence covers the code only, not the data. Most of the
data comes from U.S. federal agencies and is in the public domain. Natural Earth is public domain. The faith lenses
are figures derived from the 2020 U.S. Religion Census (© 2022 ASARB). They are used under the ARDA usage terms,
which ask for credit to the Association of Statisticians of American Religious Bodies and the Association of
Religion Data Archives. Please keep that credit if you reuse them.

The look is inspired by turn-based 4X strategy games, but every glyph, icon and texture here is original and drawn
in code. This project is not affiliated with or endorsed by any game publisher.

## Ideas

- A county-level elections lens.
- Alaska and Hawaii insets, so the whole country is on the map.
- USDA crops as agricultural resources.
- A county "Specialisation" lens from `districts.json`.
- A time slider: yearly population (2020 onwards) is already in `cities.json`, and BEA has GDP history.

Forks and experiments are welcome.
