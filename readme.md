# Purpose
Local database system to capture and edit specimen data for a personal entomological collection. 
## Key features
- data structure designed around Darwin Core and the Darwin Core Batch Importer on TaxonWorks
#### Unique Identifiers
- unique identifiers for collection objects
    - streamlined workflow to print and assign unique identifiers
#### Georeferencing
- georeference using the point-radius method, by drawing a circle on a map.
- automatic retrival of country, province, county, municipality, locality via the [Photon Geocoding API](https://photon.komoot.io/)

## How to get it running
On Linux, having python and conda installed should be sufficient.
- conda env create -f environment.yml
- conda activate collection
- alembic upgrade head (create/migrate the database)
- python run.py (starts the app at http://127.0.0.1:8080)

On Windows it will also run, but I have not tested to figure out how.
