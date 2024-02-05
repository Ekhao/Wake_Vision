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
from wake_vision_loader import get_wake_vision, get_miaps
from vww_loader import get_vww

import wandb
from wandb.keras import WandbMetricsLogger

def train(cfg=default_cfg, extra_evals=["distance_eval", "miap_eval"]):
    wandb.init(project="wake-vision", name=cfg.EXPERIMENT_NAME, config=cfg)

    if cfg.TARGET_DS == "vww":
        train, val, test = get_vww(cfg)
    else:
        train, val, test = get_wake_vision(cfg)

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

    callbacks = [WandbMetricsLogger()]

    #Distance Eval on each epoch
    if "distance_eval" in extra_evals:
        from wake_vision_loader import get_distance_eval
        class DistanceEvalCallback(tf.keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                dist_cfg = cfg.copy_and_resolve_references()
                dist_cfg.MIN_BBOX_SIZE = 0.05
                distance_ds = get_distance_eval(dist_cfg, split="validation")
                print("Distace Eval Results:")
                for name, value in distance_ds.items():
                    result = self.model.evaluate(value, verbose=0)[1]
                    print(f"{name}: {result}")
                    wandb.log({"epoch/Dist-"+name: result})
        
        callbacks.append(DistanceEvalCallback())
    if "miap_eval" in extra_evals:
        class MIAPEvalCallback(keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                miaps_validation = get_miaps(cfg, split="validation")
                print("MIAPS Eval Results:")
                for name, value in miaps_validation.items():
                    result = self.model.evaluate(value, verbose=0)[1]
                    print(f"{name}: {result}")
                    wandb.log({"epoch/MIAPS-"+name: result})
        
        callbacks.append(MIAPEvalCallback())
    

    # Train for a fixed number of steps, validating every
    model.fit(
        train, epochs=(cfg.STEPS//cfg.VAL_STEPS), steps_per_epoch=cfg.VAL_STEPS, validation_data=val,
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

    cfg = get_cfg()

    parser = argparse.ArgumentParser()
    parser.add_argument("--target_ds", type=str, default=cfg.TARGET_DS)
    parser.add_argument("--model_size", type=float, default=cfg.MODEL_SIZE)
    parser.add_argument(
        "--input_size", type=str, default=",".join(map(str, cfg.INPUT_SHAPE))
    )

    args = parser.parse_args()
    cfg.TARGET_DS = args.target_ds
    cfg.MODEL_SIZE = args.model_size
    cfg.INPUT_SHAPE = tuple(map(int, args.input_size.split(",")))

    train(cfg)
