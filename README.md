# ImageSeq – QGIS Plugin (Ita)

ImageSeq è un plugin per QGIS progettato per visualizzare in sequenza una serie di immagini raster, come immagini satellitari o dati da indagini geofisiche (ad esempio profili GPR).
L’obiettivo è permettere il confronto rapido fra più raster caricati in un progetto, mostrando un’immagine alla volta e passando all’immagine successiva tramite un controllo a manopola (dial).

## Funzionalità principali

- Importazione multipla di raster (.tif, .tiff) tramite finestra di selezione.
- Creazione automatica di un gruppo nel Layer Tree di QGIS per organizzare le immagini importate.
- Gestione della visibilità dei raster all’interno del gruppo: il plugin abilita solo un’immagine per volta.
- Navigazione sequenziale delle immagini mediante un dial, che attiva/disattiva progressivamente i raster del gruppo.
- Interfaccia semplificata con lista dei gruppi e controllo della selezione.

## Perché usarlo

ImageSeq è utile quando si lavora con:

- sequenze di immagini satellitari acquisite in momenti differenti,
- profili GPR o altri dataset geofisici acquisiti in successione,
- serie di raster che rappresentano evoluzioni temporali o variazioni spaziali,
- confronti rapidi fra immagini simili senza dover gestire manualmente la visibilità di ogni layer.

## Requisiti

QGIS 3.x
Formati raster supportati: .tif, .tiff

# ImageSeq – QGIS Plugin (Eng)

ImageSeq is a QGIS plugin designed to display a sequence of raster images, such as satellite imagery or geophysical data (e.g., GPR profiles).
Its purpose is to enable fast comparison between multiple rasters loaded into a project, showing one image at a time and moving through the sequence using a dial control.

## Main Features

- Multiple raster import (.tif, .tiff) via a file selection dialog.
- Automatic creation of a group in the QGIS Layer Tree to organize imported images.
- Visibility management of rasters inside the group: the plugin enables only one image at a time.
- Sequential navigation of images using a dial that progressively activates/deactivates raster layers.
- Simplified interface with group listing and selection control.

## Why use it

ImageSeq is useful when working with:

- sequences of satellite images acquired at different times,
- GPR profiles or other sequential geophysical datasets,
- raster series representing temporal evolution or spatial variation,
- quick comparisons between similar images without manually toggling layer visibility.

## Requirements

QGIS 3.x

Supported raster formats: .tif, .tiff
