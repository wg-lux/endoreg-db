from .inference_dataset import InferenceDataset
from torch.utils.data import DataLoader
import torch
from torch import nn
import json
from .postprocess import (
    concat_pred_dicts,
    make_smooth_preds,
    find_true_pred_sequences
)
import numpy as np

sample_config = {
    # mean and std for normalization
    "mean": (0.45211223, 0.27139644, 0.19264949),
    "std": (0.31418097, 0.21088019, 0.16059452),
    # Image Size
    "size_x": 716,
    "size_y": 716,
    # how to wrangle axes of the image before putting them in the network
    "axes": [2,0,1],  # 2,1,0 for opencv
    "batchsize": 16,
    "num_workers": 0, # always 1 for Windows systems # FIXME: fix celery crash if multiprocessing
    # maybe add sigmoid after prediction?
    "activation": nn.Sigmoid(),
    "labels": [
        'appendix',  'blood',  'diverticule',  'grasper',  'ileocaecalvalve',  'ileum',  'low_quality',  'nbi',  'needle',  'outside',  'polyp',  'snare',  'water_jet',  'wound'
    ]
}

class Classifier():
    def __init__(self, model=None, config=sample_config, verbose = False):
        self.config = config
        self.model = model
        self.verbose = verbose

    def pipe(self, paths, crops, verbose = None):
        if verbose is None:
            verbose = self.verbose

        dataset = InferenceDataset(paths, crops, self.config)
        if verbose:
            print("Dataset created")

        dl = DataLoader(
            dataset=dataset,
            batch_size=self.config["batchsize"],
            num_workers=self.config["num_workers"],
            shuffle = False,
            pin_memory=True
        )
        if verbose:
            print("Dataloader created")

        predictions = []

        with torch.inference_mode():
            if self.verbose:
                print("Starting inference")
            for i,batch in enumerate(dl):
                prediction = self.model(batch.cuda())
                prediction = self.config["activation"](prediction).cpu().tolist()#.numpy().tolist()
                predictions += prediction
                if self.verbose and i==0:
                    print("First batch done")

        return predictions

    def __call__(self, image, crop=None):
        return self.pipe([image], [crop])

    def readable(self, predictions):
        return {label: prediction for label, prediction in zip(self.config["labels"], predictions)}
    
    def get_prediction_dict(self, predictions, paths):
        json_dict = {
            "labels": self.config["labels"],
            "paths": paths,
            "predictions": predictions
        }
        
        return json_dict
    
    def get_prediction_json(self, predictions, paths, json_target_path: str = None):
        if not json_target_path:
            json_target_path = "predictions.json"

        json_dict = self.get_prediction_dict(predictions, paths)

        with open(json_target_path, 'w') as f:
            json.dump(json_dict, f)
        
        if self.verbose:
            print(f"Saved predictions to {json_target_path}")

    def post_process_predictions(self, pred_dicts, window_size_s=1, fps=50, min_seq_len_s = 0.5):
        '''
        pred_dicts: list of dictionaries with the same keys
        window_size_s: size of the window in seconds for smoothing
        fps: frames per second
        min_seq_len_s: minimum length of a sequence in seconds

        Returns:
        predictions: concatenated predictions
        smooth_predictions: smoothed predictions
        binary_predictions: binary predictions
        raw_sequences: raw sequences
        filtered_sequences: filtered sequences
        '''
        # Concatenate the predictions
        predictions = concat_pred_dicts(pred_dicts)

        smooth_predictions = {key:[] for key in predictions.keys()}
        for key in predictions.keys():
            smooth_predictions[key] = make_smooth_preds(
                predictions[key],
                window_size_s=window_size_s,
                fps=fps
            )

        binary_predictions = {}
        for key in smooth_predictions.keys():
            binary_predictions[key] = np.array([p > 0.5 for p in smooth_predictions[key]])

        raw_sequences = {}
        for key in binary_predictions.keys():
            raw_sequences[key] = find_true_pred_sequences(binary_predictions[key])

        filtered_sequences = {}
        min_seq_len = int(min_seq_len_s * fps)
        for key in raw_sequences.keys():
            filtered_sequences[key] = [s for s in raw_sequences[key] if s[1] - s[0] > min_seq_len]

        return predictions, smooth_predictions, binary_predictions, raw_sequences, filtered_sequences

    def post_process_predictions_serializable(
        self, pred_dicts,
        window_size_s = 1, fps = 50,
        min_seq_len_s = 0.5
    ):
        result = self.post_process_predictions(
            pred_dicts,
            window_size_s,
            fps, min_seq_len_s
        )

        for i, _dict in enumerate(result):
            _keys = list(_dict.keys())
            for key in _keys:
                # if numpy array
                if hasattr(_dict[key], "tolist"):
                    result[i][key] = _dict[key].tolist()
                
                # check if list of tuples
                # if so, make sure each tuple has 2 elements and split to two lists (start, stop)
                if all(isinstance(x, tuple) for x in _dict[key]):
                    if all(len(x) == 2 for x in _dict[key]):
                        result[i][f"{key}_start"] = [int(x[0]) for x in _dict[key]]
                        result[i][f"{key}_stop"] = [int(x[1]) for x in _dict[key]]
                        del result[i][key]
        
        # make dict of dicts
        result_dict = {
            "predictions": result[0],
            "smooth_predictions": result[1],
            "binary_predictions": result[2],
            "raw_sequences": result[3],
            "filtered_sequences": result[4]
        }

        return result_dict
                