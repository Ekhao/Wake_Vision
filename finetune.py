"""
Training Script for Wake Vision and Visual Wake Words Datasets
"""

import numpy as np
import os

os.environ["KERAS_BACKEND"] = "jax"

# Note that keras should only be imported after the backend
# has been configured. The backend cannot be changed once the
# package is imported.
import keras

import tensorflow as tf
import tensorflow_datasets as tfds

from experiment_config import default_cfg, get_cfg
from wake_vision_loader import get_wake_vision
from vww_loader import get_vww
import resnet

import wandb


def train(cfg=default_cfg, extra_evals=["distance_eval", "miap_eval", "lighting_eval"], base_model=None):
    wandb.init(
        project="wake-vision",
        name=cfg.EXPERIMENT_NAME,
        config=cfg,
    )

    if cfg.TARGET_DS == "vww":
        train, val, test = get_vww(cfg)
    elif cfg.TARGET_DS == "wv":
        train, val, test = get_wake_vision(cfg)
    elif cfg.TARGET_DS == "wv_tfds":
        if cfg.LABEL_TYPE == "image":
            train_split = "train_image"
        else:
            train_split = "train_bbox"
            
        if cfg.TRAIN_PERCENTAGE:
            train_split = f"{train_split}[0:{cfg.TRAIN_PERCENTAGE}%]"
        
        train, val, test = tfds.load(
        "wake_vision",
        data_dir=cfg.WV_DIR,
        shuffle_files=False,
        split=[train_split, "validation", "test"],
        )
            
        if cfg.ERROR_RATE:
            from pp_ops import inject_label_errors
            print("Injecting Label Errors at Rate:", cfg.ERROR_RATE)
            error_func = lambda ds_entry: (inject_label_errors(ds_entry, cfg.ERROR_RATE))
            train = train.map(error_func, num_parallel_calls=tf.data.AUTOTUNE)
            
        from wake_vision_loader import preprocessing
        train = preprocessing(train, cfg.BATCH_SIZE, train=True, cfg=cfg)
        val = val.filter(lambda x: x['person'] >= 0) #Filter out far set images
        val = preprocessing(val, cfg.BATCH_SIZE, train=False, cfg=cfg)
        test = test.filter(lambda x: x['person'] >= 0) #Filter out far set images
        test = preprocessing(test, cfg.BATCH_SIZE, train=False, cfg=cfg)
        
    else:
        raise ValueError('Invalid target dataset. Must be either "vww" or "wv".')
    
    if base_model: 
        import yaml
        model_yaml = "gs://wake-vision-storage-2/saved_models/" + base_model + "/config.yaml"
        with tf.io.gfile.GFile(model_yaml, 'r') as fp:
            base_model_cfg = yaml.unsafe_load(fp)
        model_path = base_model_cfg.SAVE_FILE
        model = keras.saving.load_model(model_path)

    elif cfg.MODEL == "resnet_mlperf":
        model = resnet.resnet_mlperf(
            input_shape=cfg.INPUT_SHAPE,
            num_classes=cfg.NUM_CLASSES,
        )
    elif cfg.MODEL == "resnet18":
        model = resnet.resnet18(
            input_shape=cfg.INPUT_SHAPE,
            num_classes=cfg.NUM_CLASSES,
        )
    elif cfg.MODEL == "resnet34":
        model = resnet.resnet34(
            input_shape=cfg.INPUT_SHAPE,
            num_classes=cfg.NUM_CLASSES,
        )
    elif cfg.MODEL == "resnet50":
        model = keras.applications.ResNet50(
            input_shape=cfg.INPUT_SHAPE,
            weights=None,
            classes=cfg.NUM_CLASSES,
        )
    elif cfg.MODEL == "resnet101":
        model = keras.applications.ResNet101(
            input_shape=cfg.INPUT_SHAPE,
            weights=None,
            classes=cfg.NUM_CLASSES,
        )
    else:
        model = keras.applications.MobileNetV2(
            input_shape=cfg.INPUT_SHAPE,
            alpha=cfg.MODEL_SIZE,
            weights=None,
            classes=cfg.NUM_CLASSES,
        )

    """
    Here's our model summary:
    """

    model.summary()

    """
    We use the `compile()` method to specify the optimizer, loss function,
    and the metrics to monitor. Note that with the JAX and TensorFlow backends,
    XLA compilation is turned on by default.
    """
    lr_schedule = keras.optimizers.schedules.CosineDecay(
        cfg.INIT_LR,
        decay_steps=cfg.DECAY_STEPS,
        alpha=0.0,
        warmup_target=cfg.LR,
        warmup_steps=cfg.WARMUP_STEPS,
    )

    model.compile(
        loss=keras.losses.SparseCategoricalCrossentropy(),
        optimizer=keras.optimizers.AdamW(
            learning_rate=lr_schedule, weight_decay=cfg.WEIGHT_DECAY
        ),
        metrics=[
            keras.metrics.SparseCategoricalAccuracy(name="acc"),
        ],
    )

    callbacks = [wandb.keras.WandbMetricsLogger()]

    # Distance Eval on each epoch
    if "distance_eval" in extra_evals:
        from wake_vision_loader import get_distance_eval

        class DistanceEvalCallback(tf.keras.callbacks.Callback):
            def __init__(self):
                print("Distance Validation Callback On")
                self.f1_score = tf.keras.metrics.F1Score(threshold=0.5)

            def on_epoch_end(self, epoch, logs=None):
                dist_cfg = cfg.copy_and_resolve_references()
                dist_cfg.MIN_BBOX_SIZE = 0.05
                distance_ds = get_distance_eval(dist_cfg, split="validation")
                print("\nDistace Eval Results:")
                for name, value in distance_ds.items():
                    predictions = self.model.predict(value, verbose=0)
                    unbatched_value = value.unbatch()
                    sparse_true_labels = unbatched_value.map(
                        lambda x, y: y, num_parallel_calls=tf.data.AUTOTUNE
                    )
                    one_hot_true_labels = tf.one_hot(
                        list(sparse_true_labels.as_numpy_iterator()), 2
                    )
                    self.f1_score.update_state(one_hot_true_labels, predictions)
                    print(f"{name}: {self.f1_score.result()[1]}")
                    wandb.log({"epoch/Dist-" + name: self.f1_score.result()[1]})
                    self.f1_score.reset_state()

        callbacks.append(DistanceEvalCallback())
    if "miap_eval" in extra_evals:
        from wake_vision_loader import get_miaps

        class MIAPEvalCallback(keras.callbacks.Callback):
            def __init__(self):
                print("MIAP Validation Callback On")
                self.f1_score = tf.keras.metrics.F1Score(threshold=0.5)

            def on_epoch_end(self, epoch, logs=None):
                miaps_validation = get_miaps(cfg, split="validation")
                print("MIAPS Eval Results:")
                for name, value in miaps_validation.items():
                    predictions = self.model.predict(value, verbose=0)
                    unbatched_value = value.unbatch()
                    sparse_true_labels = unbatched_value.map(
                        lambda x, y: y, num_parallel_calls=tf.data.AUTOTUNE
                    )
                    one_hot_true_labels = tf.one_hot(
                        list(sparse_true_labels.as_numpy_iterator()), 2
                    )
                    self.f1_score.update_state(one_hot_true_labels, predictions)
                    print(f"{name}: {self.f1_score.result()[1]}")
                    wandb.log({"epoch/MIAPs-" + name: self.f1_score.result()[1]})
                    self.f1_score.reset_state()

        callbacks.append(MIAPEvalCallback())

    if "lighting_eval" in extra_evals:
        from wake_vision_loader import get_lighting

        class LightingEvalCallback(keras.callbacks.Callback):
            def __init__(self):
                self.f1_score = tf.keras.metrics.F1Score(threshold=0.5)
                print("Lighting Validation Callback On")

            def on_epoch_end(self, epoch, logs=None):
                lighting_ds = get_lighting(cfg, split="validation")
                print("Lighting Eval Results:")
                for name, value in lighting_ds.items():
                    predictions = self.model.predict(value, verbose=0)
                    unbatched_value = value.unbatch()
                    sparse_true_labels = unbatched_value.map(
                        lambda x, y: y, num_parallel_calls=tf.data.AUTOTUNE
                    )
                    one_hot_true_labels = tf.one_hot(
                        list(sparse_true_labels.as_numpy_iterator()), 2
                    )
                    self.f1_score.update_state(one_hot_true_labels, predictions)
                    print(f"{name}: {self.f1_score.result()[1]}")
                    wandb.log({"epoch/Lighting-" + name: self.f1_score.result()[1]})
                    self.f1_score.reset_state()

        callbacks.append(LightingEvalCallback())

    # Train for a fixed number of steps, validating every
    model.fit(
        train,
        epochs=(cfg.STEPS // cfg.VAL_STEPS),
        steps_per_epoch=cfg.VAL_STEPS,
        validation_data=val,
        callbacks=callbacks,
    )
    score = model.evaluate(test, verbose=1)
    print(score)

    model.save(cfg.SAVE_FILE)
    with tf.io.gfile.GFile(f"{cfg.SAVE_DIR}config.yaml", "w") as fp:
        cfg.to_yaml(stream=fp)

    # return path to saved model, to be evaluated
    wandb.finish()
    return cfg.SAVE_FILE


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--experiment_name", type=str)
    parser.add_argument("-t", "--target_ds", type=str)
    parser.add_argument("-l", "--label_type", type=str)
    parser.add_argument("-ms", "--model_size", type=float)
    parser.add_argument("-is", "--input_size", type=str)
    parser.add_argument("-g", "--grayscale", type=bool)
    parser.add_argument("-m", "--model", type=str)
    parser.add_argument("-lr", "--lr", type=float)
    parser.add_argument("-e", "--error_rate", type=float)
    parser.add_argument("-p", "--dataset_percentage", type=int)
    parser.add_argument("--base_model", type=str)
    parser.add_argument("-wd", "--weight_decay", type=float)
    

    args = parser.parse_args()
    cfg = get_cfg(args.experiment_name, args.model)
    if args.target_ds:
        cfg.TARGET_DS = args.target_ds
    if args.label_type:
        cfg.LABEL_TYPE = args.label_type
    if args.model_size:
        cfg.MODEL_SIZE = args.model_size
    if args.input_size:
        cfg.INPUT_SHAPE = tuple(map(int, args.input_size.split(",")))
    if args.grayscale:
        cfg.grayscale = args.grayscale
    if args.lr:
        cfg.LR = args.lr
    if args.error_rate:
        cfg.ERROR_RATE = args.error_rate
    if args.dataset_percentage:
        cfg.TRAIN_PERCENTAGE = args.dataset_percentage
    if args.weight_decay:
        cfg.WEIGHT_DECAY = args.weight_decay

    train(cfg, extra_evals=[], base_model=args.base_model)