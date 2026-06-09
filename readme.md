# Purpose
Local database system to capture and edit specimen data for a personal entomological collection.  
Main goal is close integration with the weevil project on TaxonWorks, with the aim to export specimen data to TaxonWorks and to track subsequent diversions in the datasets via regular API comparison. This will highlight e.g. cases when I reidentified a specimen in my database to remind me to update on TaxonWorks as well.

Based on python, user interface in an internet browser.
## Key features
- data structure designed around Darwin Core and the Darwin Core Batch Importer on TaxonWorks
#### Taxonomy Import
- For weevils from TaxonWorks
- For plants from TaxonWorks (for compatibility, more taxonomic ranks if present on TaxonWorks) or POWO
#### Unique Identifiers (in progress)
- unique identifiers for collection objects
    - streamlined workflow to print and assign unique identifiers
#### Georeferencing
- georeference using the point-radius method, by drawing a circle on a map.
- Based on the coordinates: Automatic retrival of country, province, county, municipality, locality via the [Photon Geocoding API](https://photon.komoot.io/)
#### Data Integrity
- Controlled vocabularies (currently only for Persons): Helping to keep data consistent. If you made a mistake (like adding both P Müller and Peter Müller), you can merge them and all data is re-linked to the remaining name.
#### Workflows
- **Specimen Digitization**: Digitize Specimens based on the Darwin Core Format
- 
- **Import & Assign**: Assign unique identifier labels to a collection that was digitized without unique identifiers for specimens

## Planned features:
- Digitize specimens from foreign collections (e.g. museum visit)
- Print Queue, smarter printing
- Add media files to collecting events and specimens
- Better representation for specimens that were reared: Create "near-duplicates" of a collection object that are human observations and may have different life stages. E.g. you may have a beetle in your collection, "collected" June 1 2020. Add a corresponding larva, "humanObservation" instead of "collectionObject", that was observed May 2 2020 feeding on Trifolium. At least identifications should be shared between both. 

## How to get it running
On Linux, having python and conda installed should be sufficient for a start to move forward:

```bash
conda env create -f environment.yml # create a new conda environment from the template file, to install dependencies
conda activate collection # activate the environment
alembic upgrade head       # create/migrate the database
python run.py              # starts the app at http://127.0.0.1:8080
```
**After those steps, it is sufficient to execute run.py with the conda environment activated.**  
For convenience, it is best to have a bash scrip that activates the conda environemnt and starts the program with one click. You can add launch.sh to your systems task bar or start menu, but you may have to adjust paths in the file to make it run on your system.

On Windows it will also run, but I have not tested to figure out how.
