# Copyright 2020 The SQLFlow Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License

import os
import traceback
import types

import runtime
import tensorflow as tf
from runtime import db, oss
from runtime.diagnostics import SQLFlowDiagnostic
from runtime.pai.pai_distributed import define_tf_flags
from runtime.tensorflow import is_tf_estimator
from runtime.tensorflow.predict import estimator_predict, keras_predict
from tensorflow.estimator import (BoostedTreesClassifier,
                                  BoostedTreesRegressor, DNNClassifier,
                                  DNNLinearCombinedClassifier,
                                  DNNLinearCombinedRegressor, DNNRegressor,
                                  LinearClassifier, LinearRegressor)

try:
    import sqlflow_models
except Exception as e:
    print("error importing sqlflow_models: %s" % e)
    traceback.print_exc()


def predict_tf(datasource, select, data_table, result_table, label_column,
               oss_model_path):
    """PAI Tensorflow prediction wrapper
    This function do some preparation for the local prediction, say, download the
    model from OSS, extract metadata and so on.

    Args:
        datasource: the datasource from which to get data
        select: data selection SQL statement
        data_table: tmp table which holds the data from select
        result_table: table to save prediction result
        label_column: prediction label column
        oss_model_path: the model path on OSS
    """

    try:
        tf.enable_eager_execution()
    except:
        pass

    (estimator, feature_column_names, feature_column_names_map, feature_metas,
     label_meta, model_params,
     feature_columns_code) = oss.load_metas(oss_model_path,
                                            "tensorflow_model_desc")

    feature_columns = eval(feature_columns_code)

    # NOTE(typhoonzero): No need to eval model_params["optimizer"] and model_params["loss"]
    # because predicting do not need these parameters.

    is_estimator = is_tf_estimator(eval(estimator))

    # Keras single node is using h5 format to save the model, no need to deal with export model format.
    # Keras distributed mode will use estimator, so this is also needed.
    if is_estimator:
        oss.load_file(oss_model_path, "exported_path")
        # NOTE(typhoonzero): directory "model_save" is hardcoded in codegen/tensorflow/codegen.go
        oss.load_dir("%s/model_save" % oss_model_path)
    else:
        oss.load_file(oss_model_path, "model_save")

    pred(datasource=datasource,
         estimator_string=estimator,
         select=select,
         result_table=result_table,
         feature_columns=feature_columns,
         feature_column_names=feature_column_names,
         feature_column_names_map=feature_column_names_map,
         train_label_name=label_meta["feature_name"],
         result_col_name=label_column,
         feature_metas=feature_metas,
         model_params=model_params,
         save="model_save",
         batch_size=1,
         pai_table=data_table)


def pred(datasource,
         estimator_string,
         select,
         result_table,
         feature_columns,
         feature_column_names,
         feature_column_names_map,
         train_label_name,
         result_col_name,
         feature_metas={},
         model_params={},
         save="",
         batch_size=1,
         pai_table=""):
    runtime.import_model_def(estimator_string, globals())
    estimator = eval(estimator_string)
    model_params.update(feature_columns)
    is_estimator = is_tf_estimator(estimator)

    conn = None
    driver = "pai_maxcompute"
    pai_table_parts = pai_table.split(".")
    formatted_pai_table = "odps://%s/tables/%s" % (pai_table_parts[0],
                                                   pai_table_parts[1])
    selected_cols = db.pai_selected_cols(formatted_pai_table)
    predict_generator = db.pai_maxcompute_db_generator(formatted_pai_table)

    if not is_estimator:
        if not issubclass(estimator, tf.keras.Model):
            # functional model need field_metas parameter
            model_params["field_metas"] = feature_metas
        print("Start predicting using keras model...")
        keras_predict(estimator,
                      model_params,
                      save,
                      result_table,
                      feature_column_names,
                      feature_metas,
                      train_label_name,
                      result_col_name,
                      driver,
                      conn,
                      predict_generator,
                      selected_cols,
                      hdfs_namenode_addr="",
                      hive_location="",
                      hdfs_user="",
                      hdfs_pass="")
    else:
        model_params['model_dir'] = save
        print("Start predicting using estimator model...")
        estimator_predict(estimator,
                          model_params,
                          save,
                          result_table,
                          feature_column_names,
                          feature_column_names_map,
                          feature_columns,
                          feature_metas,
                          train_label_name,
                          result_col_name,
                          driver,
                          conn,
                          predict_generator,
                          selected_cols,
                          hdfs_namenode_addr="",
                          hive_location="",
                          hdfs_user="",
                          hdfs_pass="")

    print("Done predicting. Predict table : %s" % result_table)
