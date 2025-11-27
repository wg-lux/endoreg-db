# File Import and Anonymization

Endoreg-Db imports are guarded by a anonymization step, that is supposed to ensure most data is redacted from the input. Here, fake patients are generated to pseudonymize the sensitive information in the data. This ensures, that videos as well as pdfs are not distributed using sensitive data, and if they are by some accident it is harder to know what data is actually real.

The Import is handled by two orchestration files:

Report import service (RIS)

and 

Video import Service (VIS)

The orchestration is abstracted out by the base import service (BIS), to ensure newly implemented data imports follow the same structure and to ensure tests run agnostically of the actual media being processed.

## Import Order of Execution

The Import starts, when files are dropped into the corresponding media import folders. The locations need to be passed to the import service logic. To ensure atomic processing without overwhelming the server or double processing on parallelization, a file lock is added to the files that are currently processed.

### File Lock

File Lock is implemented as a context manager. Per default, this means during the execution the files are marked by adding a additional .lock file path inside the folder. Once the code wrapped in the context manager of file lock stops execution, the .lock file is removed only after error processing. This ensures, the full pipeline is executed on each run even when interrupted. 
https://book.pythontips.com/en/latest/context_managers.html

### Error Cleanup

The ErrorCleanup class is called from inside the file lock context manager to avoid leaving half processed files laying around. It passes file type to the class instance and then runs the correct processing logic.