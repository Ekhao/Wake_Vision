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

import wandb
from wandb.keras import WandbMetricsLogger


def train(cfg=default_cfg, extra_evals=["distance_eval", "miap_eval", "lighting_eval"]):
    wandb.init(
        entity="harvard-edge",
        project="wake-vision",
        name=cfg.EXPERIMENT_NAME,
        config=cfg,
    )

    if cfg.TARGET_DS == "vww":
        train, val, test = get_vww(cfg)
    elif cfg.TARGET_DS == "wv":
        train, val, test = get_wake_vision(cfg)
    else:
        raise ValueError("Invalid target dataset. Must be either \"vww\" or \"wv\".")

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

    # Distance Eval on each epoch
    if "distance_eval" in extra_evals:
        from wake_vision_loader import get_distance_eval

        class DistanceEvalCallback(tf.keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                distance_ds = get_distance_eval(cfg, split="validation")
                print("\nDistace Eval Results:")
                for name, value in distance_ds.items():
                    result = self.model.evaluate(value, verbose=0)[1]
                    print(f"{name}: {result}")
                    wandb.log({"epoch/Dist-" + name: result})

        callbacks.append(DistanceEvalCallback())
    if "miap_eval" in extra_evals:
        from wake_vision_loader import get_miaps

        class MIAPEvalCallback(keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                miaps_validation = get_miaps(cfg, split="validation")
                print("MIAPS Eval Results:")
                for name, value in miaps_validation.items():
                    result = self.model.evaluate(value, verbose=0)[1]
                    print(f"{name}: {result}")
                    wandb.log({"epoch/MIAPS-" + name: result})

        callbacks.append(MIAPEvalCallback())

    if "lighting_eval" in extra_evals:
        from wake_vision_loader import get_lighting

        class LightingEvalCallback(keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                lighting_ds = get_lighting(cfg, split="validation")
                print("Lighting Eval Results:")
                for name, value in lighting_ds.items():
                    result = self.model.evaluate(value, verbose=0)[1]
                    print(f"{name}: {result}")
                    wandb.log({"epoch/Lighting-" + name: result})

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

    args = parser.parse_args()
    cfg = get_cfg(args.experiment_name)
    if args.target_ds:
        cfg.TARGET_DS = args.target_ds
    if args.label_type:
        cfg.LABEL_TYPE = args.label_type
    if args.model_size:
        cfg.MODEL_SIZE = args.model_size
    if args.input_size:
        cfg.INPUT_SHAPE = tuple(map(int, args.input_size.split(",")))

    train(cfg)
