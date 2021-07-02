import numpy as np
import tensorflow as tf

from ncem.estimators import EstimatorGraph
from ncem.models import ModelCVAEncem


class EstimatorCVAEncem(EstimatorGraph):
    def __init__(
        self,
        cond_type: str = "gcn",
        use_type_cond: bool = True,
        log_transform: bool = False,
    ):
        super(EstimatorCVAEncem, self).__init__()
        self.model_type = "cvae_ncem"

        if cond_type == "gcn":
            self.adj_type = "scaled"
        elif cond_type == "max":
            self.adj_type = "full"
        else:
            raise ValueError("cond_type %s not recognized" % cond_type)
        self.cond_type = cond_type
        self.use_type_cond = use_type_cond
        self.log_transform = log_transform

    def init_model(
        self,
        optimizer: str = "adam",
        learning_rate: float = 0.0001,
        latent_dim: int = 8,
        intermediate_dim_enc: int = 128,
        intermediate_dim_dec: int = 128,
        depth_enc: int = 1,
        depth_dec: int = 1,
        dropout_rate: float = 0.1,
        l2_coef: float = 0.0,
        l1_coef: float = 0.0,
        cond_depth: int = 1,
        cond_dim: int = 8,
        cond_dropout_rate: float = 0.1,
        cond_activation: str = "relu",
        cond_l2_reg: float = 0.0,
        cond_use_bias: bool = False,
        n_eval_nodes_per_graph: int = 32,
        use_domain: bool = False,
        use_batch_norm: bool = False,
        scale_node_size: bool = True,
        transform_input: bool = False,
        beta: float = 0.01,
        max_beta: float = 1.0,
        pre_warm_up: int = 0,
        output_layer: str = "gaussian",
        **kwargs
    ):
        self.n_eval_nodes_per_graph = n_eval_nodes_per_graph
        self.model = ModelCVAEncem(
            input_shapes=(
                self.n_features_0,
                self.n_features_1,
                self.max_nodes,
                self.n_eval_nodes_per_graph,
                self.n_node_covariates,
                self.n_domains,
            ),
            latent_dim=latent_dim,
            intermediate_dim_enc=intermediate_dim_enc,
            intermediate_dim_dec=intermediate_dim_dec,
            depth_enc=depth_enc,
            depth_dec=depth_dec,
            dropout_rate=dropout_rate,
            l2_coef=l2_coef,
            l1_coef=l1_coef,
            cond_type=self.cond_type,
            cond_depth=cond_depth,
            cond_dim=cond_dim,
            cond_dropout_rate=cond_dropout_rate,
            cond_activation=cond_activation,
            cond_l2_reg=cond_l2_reg,
            cond_use_bias=cond_use_bias,
            use_domain=use_domain,
            use_type_cond=self.use_type_cond,
            use_batch_norm=use_batch_norm,
            scale_node_size=scale_node_size,
            transform_input=transform_input,
            output_layer=output_layer,
        )
        optimizer = tf.keras.optimizers.get(optimizer)
        tf.keras.backend.set_value(optimizer.lr, learning_rate)
        self.cond_depth = cond_depth
        self.beta = beta
        self.max_beta = max_beta
        self.pre_warm_up = pre_warm_up
        self._compile_model(optimizer=optimizer, output_layer=output_layer)
        self.optimizer = optimizer

    def evaluate_any_posterior_sampling(self, img_keys, node_idx, batch_size: int = 1):
        # generating a resampled dataset for neighbourhood transfer evaluation
        ds = self._get_resampled_dataset(image_keys=img_keys, nodes_idx=node_idx, batch_size=batch_size, seed=None)
        eval_posterior = []
        true = []
        pred = []

        latent_z = []
        latent_z_mean = []
        latent_z_log_var = []
        for step, (x_batch, y_batch, resampled_x_batch, resampled_y_batch) in enumerate(ds):
            (h_1, sf, h_0, h_0_full, a, a_full, node_covar, g) = x_batch
            (
                h_1_resampled,
                sf_resampled,
                h_0_resampled,
                h_0_full,
                a_resampled,
                a_full,
                node_covar_resampled,
                g,
            ) = resampled_x_batch

            a_resampled = tf.sparse.reorder(a_resampled)
            z, z_mean, z_log_var = self.model.encoder((h_1, h_0, h_0_full, a, a_full, node_covar, g))

            latent_z.append(z)
            latent_z_mean.append(z_mean)
            latent_z_log_var.append(z_log_var)

            z = tf.reshape(z, [batch_size, self.n_eval_nodes_per_graph, -1])
            results = self.model.decoder.evaluate(
                (z, sf_resampled, h_0_resampled, h_0_full, a_resampled, a_full, node_covar_resampled, g),
                resampled_y_batch,
            )
            prediction = self.model.decoder.predict(
                (z, sf_resampled, h_0_resampled, h_0_full, a_resampled, a_full, node_covar_resampled, g)
            )[0]
            eval_posterior.append(results)
            true.append(h_1_resampled.numpy().squeeze())
            pred.append(prediction.squeeze())

        eval_posterior = np.concatenate(np.expand_dims(eval_posterior, axis=0), axis=0)
        eval_posterior = np.mean(eval_posterior, axis=0)
        true = np.concatenate(true, axis=0)
        pred = np.split(np.concatenate(pred, axis=0), indices_or_sections=2, axis=-1)[0]

        latent_z = np.concatenate(latent_z, axis=0)
        latent_z_mean = np.concatenate(latent_z_mean, axis=0)
        latent_z_log_var = np.concatenate(latent_z_log_var, axis=0)
        return (
            dict(zip(self.model.decoder.metrics_names, eval_posterior)),
            true,
            pred,
            latent_z,
            latent_z_mean,
            latent_z_log_var,
        )
