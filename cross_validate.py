import numpy as np
import os

os.environ["KERAS_BACKEND"] = "jax"

# Note that keras_core should only be imported after the backend
# has been configured. The backend cannot be changed once the
# package is imported.
import keras_core as keras

import tensorflow as tf
import tensorflow_datasets as tfds

import experiment_config as cfg
from wake_vision_loader import get_wake_vision
from vww_loader import get_vww

vww_model = keras.saving.load_model("../vww_cnn.keras")
wv_model = keras.saving.load_model("../wv_cnn.keras")


_, _, vww_test = get_vww(cfg.BATCH_SIZE)
_, _, wv_test = get_wake_vision(cfg.BATCH_SIZE)

vww_model_vww_test_score = vww_model.evaluate(vww_test, verbose=1)
vww_model_wv_test_score = vww_model.evaluate(wv_test, verbose=1)

wv_model_vww_test_score = wv_model.evaluate(vww_test, verbose=1)
wv_model_wv_test_score = wv_model.evaluate(wv_test, verbose=1)


cross_val = np.array([
    ["","Train", "VWW", "WV"],
    ["Test","VWW", vww_model_vww_test_score, wv_model_vww_test_score],
    ["Test", "WV", vww_model_wv_test_score, wv_model_wv_test_score],
    ["","", "", ""],
],dtype=object)

print("Cross Val Results:")
print(cross_val)