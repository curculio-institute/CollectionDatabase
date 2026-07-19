# Purpose
Local database system to capture and edit specimen data for a personal entomological collection.  
Main goal is close integration with the [weevil project on TaxonWorks](https://catalog.curculionoidea.org), with the aim to export specimen data to TaxonWorks and to track subsequent diversions in the datasets via regular API comparison. This will highlight e.g. cases when I reidentified a specimen in my database to remind me to update on TaxonWorks as well.

Based on python, user interface in an internet browser.
## Key features
Data structure designed around Darwin Core and the [Darwin Core Batch Importer on TaxonWorks](https://docs.taxonworks.org/guide/import.html)
#### Taxonomy Import
Search taxon names, import them with synonymy status, authorship and their parent taxa if not already present in the local database.
- For weevils from [weevil project on TaxonWorks](https://catalog.curculionoidea.org)
- For plants also from [weevil project on TaxonWorks](https://catalog.curculionoidea.org) (compatibility, more taxonomic ranks possible) or the [World Checklist of Vascular Plants](https://powo.science.kew.org/about-wcvp), whose archive is downloaded once from within the app and then searched offline.
#### Unique Identifiers
- unique identifiers for collection objects
    - streamlined workflow to print and assign unique identifiers
#### Georeferencing
- georeference using the point-radius method, by drawing a circle on a map.
- Based on the coordinates: Automatic retrieval of country, province, region, county and municipality via the [Overpass API](https://overpass-api.de/), which returns the administrative areas that actually contain the point, and of the locality via the [Photon Geocoding API](https://photon.komoot.io/), which finds named features near it.
- A warning when the uncertainty circle reaches across an administrative boundary.
#### Biological Associations
- Using the definitions for Biological Relationships (e.g. "collected from" or "feeding observed in the wild on") that we defined for TaxonWorks. The definitions get updated live from TaxonWorks via the API.
#### Data Integrity
- Controlled vocabularies (persons, collections, preparations, dispositions, habitats, sampling protocols, and the geography levels country, province, region, county and island): Helping to keep data consistent. If you made a mistake (like adding both P Müller and Peter Müller), you can merge them and all data is re-linked to the remaining name.
- Countries and provinces additionally carry their ISO code, so two places that share a name (like Limburg in Belgium and in the Netherlands) stay apart.
- The database is snapshotted at every launch and checked for corruption before the app serves a page. A warning appears whenever a form holds unsaved data.
#### Workflows
- **Specimen Digitization**: Digitize Specimens based on the Darwin Core Format
- **Mounting Session**: Stage several specimens that share a collecting event and print their labels in one go
- **Digitize other Collection**: Record specimens from a foreign collection, e.g. during a museum visit
- **Import & Assign**: Assign unique identifier labels to a collection that was digitized without unique identifiers for specimens
- **Explore**: Browse the collection as a taxonomic checklist, search it by taxon, geography or collector, and export the result
- **Batch tools**: Apply one change (e.g. a disposition, or a move to another collection) to many specimens at once
#### Media and other attachments
- Add media files (images, sound, video, documents, sequences) to collecting events, specimens and biological associations. Files are copied into a managed folder, so the original may be moved or deleted afterwards.
- Link a specimen to an external resource, e.g. an iNaturalist observation.
- Specimens that were reared: The preserved beetle keeps its own record, and the earlier life stages it was collected in (e.g. a larva, observed May 2 2020) are recorded on it as a life-stage history rather than as duplicate specimens.
#### Printing
- Print Queue: all staged labels are printed together on one sheet, with the data, identifier and determination label of a specimen aligned in a column.
- A label can be edited before printing without changing the record it was composed from.
#### Micro-Features
- Notifications about errors, fading by themselves unless you hover with mouse cursor above them

## Planned features:
- Export to Darwin Core and upload to TaxonWorks, then compare both datasets regularly via the API. This is the main goal and is not built yet.
- Map view of the collection, and tools to analyse the data
- Bulk-import of the existing spreadsheet dataset
- Enrich collecting events with a habitat classification, by intersecting the coordinates with a habitat map

## How to get it running
On Linux, having python and conda installed should be sufficient for a start to move forward:

```bash
git clone https://github.com/curculio-institute/CollectionDatabase # or just download the directory through your browser
cd ./CollectionDatabase # enter the directory
conda env create -f environment.yml # create a new conda environment from the template file, to install dependencies
conda activate collection # activate the environment
python run.py              # starts the app at http://127.0.0.1:8080
```
run.py creates the database on first start and migrates it on every later start, so there is nothing to set up by hand.

**After those steps, it is sufficient to execute run.py with the conda environment activated.**  
For convenience, it is best to have a bash script that activates the conda environment and starts the program with one click. You can add launch.sh to your systems task bar or start menu, but you may have to adjust paths in the file to make it run on your system.

On Windows it will also run, but I have not tested to figure out how.

## First steps
In settings, add the TaxonWorks and TaxonPages URLs and the API token. Add a collection under Controlled Vocabularies and mark it as the default collection in settings — it stamps every new specimen and gives the catalog numbers their prefix. If you record host plants, download the World Checklist of Vascular Plants from the settings as well.
