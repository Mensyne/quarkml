# -*- coding: utf-8 -*-
# @Time   : 2023/5/29 15:47
# @Author : zip
# @Moto   : Knowledge comes from decomposition
# type: ignore
from __future__ import absolute_import, division, print_function

import os
import pickle
import numpy as np
import time
import ray
from ray.util.multiprocessing import Pool
from loguru import logger
import pandas as pd
from sklearn.model_selection import StratifiedKFold
from quarkml.model.tree_model import lgb_train
from typing import List
from quarkml.utils import get_cat_num_features, error_callback
import warnings

warnings.filterwarnings(action='ignore', category=UserWarning)


class TModelSelector(object):

    def __init__(self):
        pass

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.DataFrame,
        cat_features: List = None,
        params=None,
        importance_metric: str = "importance",
        folds=5,
        seed=2023,
        distributed_and_multiprocess=-1,
    ):
        """ method : 对于n次交叉后，取 n次的最大，还是n次的平均，mean , max
            params : 模型参数
            importance_metric : 特征重要性判断依据 importance, permutation, shap, all
            report_dir: 将保存交叉验证后的每个特征的重要性结果
            is_distributed: 分布式采用ray进行交叉验证的每次结果，否则将进行多进程的pool池模式
        """
        categorical_features, _ = get_cat_num_features(X, cat_features)
        job = os.cpu_count() - 2

        kf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)

        # 【注意】这里，可以采用多进程模型 pool 池，也可以采用ray 的多进程和分布式
        if distributed_and_multiprocess == 1:
            lgb_train_remote = ray.remote(lgb_train)
        elif distributed_and_multiprocess == 2:
            pool = Pool(job)

        futures_list = []
        for i, (t_index, v_index) in enumerate(kf.split(X, y)):
            logger.info(
                f'************************************ {i + 1} ************************************'
            )

            trn_x = X.iloc[t_index]
            trn_y = y.iloc[t_index]

            val_x = X.iloc[v_index]
            val_y = y.iloc[v_index]

            trn_init_score = None
            val_init_score = None

            if distributed_and_multiprocess == 1:
                futures = lgb_train_remote.remote(
                    trn_x,
                    trn_y,
                    val_x,
                    val_y,
                    categorical_features,
                    params,
                    trn_init_score,
                    val_init_score,
                    importance_metric,
                    seed,
                )
            elif distributed_and_multiprocess == 2:
                futures = pool.apply_async(lgb_train, (
                    trn_x,
                    trn_y,
                    val_x,
                    val_y,
                    categorical_features,
                    params,
                    trn_init_score,
                    val_init_score,
                    importance_metric,
                    seed,
                ), error_callback=error_callback)
            else:
                futures = lgb_train(
                    trn_x,
                    trn_y,
                    val_x,
                    val_y,
                    categorical_features,
                    params,
                    trn_init_score,
                    val_init_score,
                    importance_metric,
                    seed,
                )
            futures_list.append(futures)

        if distributed_and_multiprocess == 2:
            pool.close()
            pool.join()

        featrue_importance = {}
        featrue_permutation = {}
        featrue_shap = {}

        if distributed_and_multiprocess == 1:
            futures_list = [_ for _ in ray.get(futures_list)]
        elif distributed_and_multiprocess == 2:
            futures_list = [_.get() for _ in futures_list]

        for items in futures_list:
            for k, v in items[1]["featrue_importance"].items():
                try:
                    featrue_importance[k].append(v)
                except KeyError:
                    featrue_importance[k] = [v]

            for k, v in items[1]["featrue_permutation"].items():
                try:
                    featrue_permutation[k].append(v)
                except KeyError:
                    featrue_permutation[k] = [v]

            for k, v in items[1]["featrue_shap"].items():
                try:
                    featrue_shap[k].append(v)
                except KeyError:
                    featrue_shap[k] = [v]

        for k, v in featrue_importance.items():
            featrue_importance[k] = np.mean(v)

        for k, v in featrue_permutation.items():
            featrue_permutation[k] = np.mean(v)

        for k, v in featrue_shap.items():
            featrue_shap[k] = np.mean(v)

        featrue_importance = sorted(featrue_importance.items(),
                                    key=lambda _: _[1],
                                    reverse=True)
        featrue_permutation = sorted(featrue_permutation.items(),
                                     key=lambda _: _[1],
                                     reverse=True)
        featrue_shap = sorted(featrue_shap.items(),
                              key=lambda _: _[1],
                              reverse=True)

        if importance_metric == "importance":
            selected_fea = [k for k, v in featrue_importance if v > 0]
            return selected_fea, X[selected_fea], featrue_importance

        if importance_metric == "permutation":
            selected_fea = [k for k, v in featrue_permutation if v > 0]
            return selected_fea, X[selected_fea], featrue_permutation

        if importance_metric == "shap":
            selected_fea = [k for k, v in featrue_shap if v > 0]
            return selected_fea, X[selected_fea], featrue_shap

