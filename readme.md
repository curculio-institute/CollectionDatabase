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

```bash
conda env create -f environment.yml # create a new conda environment from the template file, to install dependencies
conda activate collection # activate the environment
alembic upgrade head       # create/migrate the database
python run.py              # starts the app at http://127.0.0.1:8080
```
After those steps, it is sufficient to execute run.py with the conda environment activated.  
For convenience, it is best to have a bash scrip that activates the conda environemnt and starts the program with one click. You can add launch.sh to your systems task bar or start menu, but you may have to adjust paths in the file to make it run on your system.

On Windows it will also run, but I have not tested to figure out how.
