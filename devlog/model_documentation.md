# endoreg-db-model-documentation

## AI
### AiModel
Represents a generic AI model that encapsulates high-level metadata about the model,
    including names (default, German, and English), a description, categorization details,
    and associated label sets and meta information.

        name (str): Unique name of the AI model.
        name_de (str): Optional German name of the AI model.
        name_en (str): Optional English name of the AI model.
        description (str): Optional detailed description of the AI model.
        model_type (str): Optional type/category of the AI model.
        model_subtype (str): Optional subtype within the broader model type.
        video_segmentation_labelset (VideoSegmentationLabelSet): Optional associated label set for video segmentation tasks.
        active_meta (ModelMeta): Optional reference to the currently active ModelMeta instance associated with the model.
 

### ActiveModel
ActiveModel represents an active instance of a model within the application.

Attributes:
    name (str): A unique identifier for the active model.
    model_meta (ModelMeta, optional): A reference to the metadata of the model. This field acts as a ForeignKey to the ModelMeta model and can be null.

Notes:
    - The model_meta attribute is configured with on_delete=models.SET_NULL, meaning that if the related ModelMeta record is deleted, model_meta will be set to null.
    - Type hints for model_meta are provided under a TYPE_CHECKING conditional import to avoid circular dependencies, importing ModelMeta only when type checking is performed.
Managers:
    objects (ActiveModelManager): Custom manager providing specialized query capabilities for ActiveModel instances.

