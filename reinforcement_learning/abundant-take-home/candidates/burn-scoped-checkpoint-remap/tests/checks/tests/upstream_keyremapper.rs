use burn_store::{KeyRemapper, map_indices_contiguous};
use burn_pack::Tensor as PackTensor;
#[cfg(test)]
mod tests {
    use super::*;
    use burn_core::module::ParamId;
    use burn_core::tensor::{Bytes, DType, TensorData, shape};

    fn create_test_tensor(name: &str) -> PackTensor {
        let data = TensorData {
            bytes: Bytes::from_bytes_vec(vec![0u8; 4 * 4]),
            shape: shape![2, 2],
            dtype: DType::F32,
        };
        burn_store::bridge::from_data(data, name.to_string(), Some(ParamId::new().val()))
    }

    #[test]
    fn test_key_remapper_basic() {
        let remapper = KeyRemapper::new()
            .add_pattern(r"^encoder\.", "transformer.encoder.")
            .expect("valid regex");

        let tensors = vec![
            create_test_tensor("encoder.layer1.weight"),
            create_test_tensor("decoder.layer1.weight"),
        ];

        let (remapped, transformations) = remapper.remap(tensors);

        // Check that remapped tensors exist with correct names
        assert!(
            remapped
                .iter()
                .any(|v| v.name == "transformer.encoder.layer1.weight")
        );
        assert!(remapped.iter().any(|v| v.name == "decoder.layer1.weight"));
        assert_eq!(remapped.len(), 2);

        // Check transformations
        let encoder_transform = transformations
            .iter()
            .find(|(_new, old)| old == "encoder.layer1.weight")
            .expect("should find encoder transformation");
        assert_eq!(encoder_transform.0, "transformer.encoder.layer1.weight");
    }

    #[test]
    fn test_key_remapper_multiple_patterns() {
        let remapper = KeyRemapper::new()
            .add_pattern(r"^encoder\.", "transformer.encoder.")
            .expect("valid regex")
            .add_pattern(r"\.gamma$", ".weight")
            .expect("valid regex");

        let tensors = vec![create_test_tensor("encoder.layer1.gamma")];

        let (remapped, _) = remapper.remap(tensors);

        assert!(
            remapped
                .iter()
                .any(|v| v.name == "transformer.encoder.layer1.weight")
        );
        assert_eq!(remapped.len(), 1);
    }

    #[test]
    fn test_key_remapper_from_patterns() {
        let patterns = vec![(r"^pytorch\.", "burn."), (r"\.bias$", ".bias_param")];
        let remapper = KeyRemapper::from_patterns(patterns).expect("valid patterns");

        let tensors = vec![create_test_tensor("pytorch.linear.bias")];

        let (remapped, _) = remapper.remap(tensors);

        assert!(remapped.iter().any(|v| v.name == "burn.linear.bias_param"));
    }

    #[test]
    fn test_key_remapper_empty() {
        let remapper = KeyRemapper::new();
        assert!(remapper.is_empty());

        let tensors = vec![create_test_tensor("test.weight")];

        let (remapped, transformations) = remapper.remap(tensors);

        assert!(remapped.iter().any(|v| v.name == "test.weight"));
        assert_eq!(remapped.len(), 1);
        assert_eq!(transformations.len(), 1);
        assert_eq!(
            transformations[0],
            ("test.weight".to_string(), "test.weight".to_string())
        );
    }

    #[test]
    fn test_map_indices_contiguous_basic() {
        // Simulate PyTorch nn.Sequential with Conv2d (0, 2, 4) and ReLU (1, 3, 5)
        // Only Conv2d layers have parameters
        let tensors = vec![
            create_test_tensor("fc.0.weight"),
            create_test_tensor("fc.0.bias"),
            create_test_tensor("fc.2.weight"),
            create_test_tensor("fc.2.bias"),
            create_test_tensor("fc.4.weight"),
            create_test_tensor("fc.4.bias"),
        ];

        let (reindexed, transformations) = map_indices_contiguous(tensors);

        // Check that indices are now contiguous
        assert!(reindexed.iter().any(|v| v.name == "fc.0.weight"));
        assert!(reindexed.iter().any(|v| v.name == "fc.0.bias"));
        assert!(reindexed.iter().any(|v| v.name == "fc.1.weight"));
        assert!(reindexed.iter().any(|v| v.name == "fc.1.bias"));
        assert!(reindexed.iter().any(|v| v.name == "fc.2.weight"));
        assert!(reindexed.iter().any(|v| v.name == "fc.2.bias"));
        assert_eq!(reindexed.len(), 6);

        // Check transformations
        let transform_2_to_1 = transformations
            .iter()
            .find(|(_, old)| old == "fc.2.weight")
            .expect("should find fc.2.weight transformation");
        assert_eq!(transform_2_to_1.0, "fc.1.weight");

        let transform_4_to_2 = transformations
            .iter()
            .find(|(_, old)| old == "fc.4.weight")
            .expect("should find fc.4.weight transformation");
        assert_eq!(transform_4_to_2.0, "fc.2.weight");
    }

    #[test]
    fn test_map_indices_contiguous_already_contiguous() {
        // Already contiguous indices should remain unchanged
        let tensors = vec![
            create_test_tensor("fc.0.weight"),
            create_test_tensor("fc.1.weight"),
            create_test_tensor("fc.2.weight"),
        ];

        let (reindexed, transformations) = map_indices_contiguous(tensors);

        assert!(reindexed.iter().any(|v| v.name == "fc.0.weight"));
        assert!(reindexed.iter().any(|v| v.name == "fc.1.weight"));
        assert!(reindexed.iter().any(|v| v.name == "fc.2.weight"));
        assert_eq!(reindexed.len(), 3);

        // All transformations should have same old and new paths
        for (new, old) in &transformations {
            assert_eq!(new, old);
        }
    }

    #[test]
    fn test_map_indices_contiguous_multiple_prefixes() {
        // Different prefixes should be mapped independently
        let tensors = vec![
            create_test_tensor("encoder.0.weight"),
            create_test_tensor("encoder.2.weight"),
            create_test_tensor("decoder.1.weight"),
            create_test_tensor("decoder.5.weight"),
        ];

        let (reindexed, _) = map_indices_contiguous(tensors);

        // encoder: 0, 2 -> 0, 1
        assert!(reindexed.iter().any(|v| v.name == "encoder.0.weight"));
        assert!(reindexed.iter().any(|v| v.name == "encoder.1.weight"));

        // decoder: 1, 5 -> 0, 1
        assert!(reindexed.iter().any(|v| v.name == "decoder.0.weight"));
        assert!(reindexed.iter().any(|v| v.name == "decoder.1.weight"));
    }

    #[test]
    fn test_map_indices_contiguous_no_indices() {
        // Paths without indices should remain unchanged
        let tensors = vec![
            create_test_tensor("encoder.weight"),
            create_test_tensor("decoder.bias"),
        ];

        let (reindexed, transformations) = map_indices_contiguous(tensors);

        assert!(reindexed.iter().any(|v| v.name == "encoder.weight"));
        assert!(reindexed.iter().any(|v| v.name == "decoder.bias"));

        for (new, old) in &transformations {
            assert_eq!(new, old);
        }
    }

    #[test]
    fn test_map_indices_contiguous_empty() {
        let tensors: Vec<PackTensor> = vec![];
        let (reindexed, transformations) = map_indices_contiguous(tensors);

        assert!(reindexed.is_empty());
        assert!(transformations.is_empty());
    }

    #[test]
    fn test_map_indices_contiguous_mixed_indexed_and_non_indexed() {
        // Mix of indexed and non-indexed paths
        let tensors = vec![
            create_test_tensor("fc.0.weight"),
            create_test_tensor("fc.2.weight"),
            create_test_tensor("output.weight"), // no index
        ];

        let (reindexed, _) = map_indices_contiguous(tensors);

        assert!(reindexed.iter().any(|v| v.name == "fc.0.weight"));
        assert!(reindexed.iter().any(|v| v.name == "fc.1.weight")); // 2 -> 1
        assert!(reindexed.iter().any(|v| v.name == "output.weight")); // unchanged
    }

    #[test]
    fn test_map_indices_contiguous_nested_sequential() {
        // Test nested sequential structures like:
        // feature = nn.Sequential(ConvBlock, ReLU, ConvBlock, ReLU, ConvBlock)
        // where ConvBlock = nn.Sequential(Conv2d, ReLU, Conv2d)
        //
        // This produces paths like:
        // feature.layers.0.conv_block.0.weight (layer 0, conv 0)
        // feature.layers.0.conv_block.2.weight (layer 0, conv 2 - skipping ReLU at 1)
        // feature.layers.2.conv_block.0.weight (layer 2 - skipping ReLU at 1, conv 0)
        // feature.layers.2.conv_block.2.weight (layer 2, conv 2)
        let tensors = vec![
            create_test_tensor("feature.layers.0.conv_block.0.weight"),
            create_test_tensor("feature.layers.0.conv_block.2.weight"),
            create_test_tensor("feature.layers.2.conv_block.0.weight"),
            create_test_tensor("feature.layers.2.conv_block.2.weight"),
        ];

        let (mapped, transformations) = map_indices_contiguous(tensors);

        // Expected mapping:
        // feature.layers: 0, 2 -> 0, 1
        // feature.layers.0.conv_block: 0, 2 -> 0, 1
        // feature.layers.2.conv_block: 0, 2 -> 0, 1
        //
        // Result:
        // feature.layers.0.conv_block.0.weight -> feature.layers.0.conv_block.0.weight
        // feature.layers.0.conv_block.2.weight -> feature.layers.0.conv_block.1.weight
        // feature.layers.2.conv_block.0.weight -> feature.layers.1.conv_block.0.weight
        // feature.layers.2.conv_block.2.weight -> feature.layers.1.conv_block.1.weight

        assert!(
            mapped
                .iter()
                .any(|v| v.name == "feature.layers.0.conv_block.0.weight"),
            "0.0 should stay as 0.0"
        );
        assert!(
            mapped
                .iter()
                .any(|v| v.name == "feature.layers.0.conv_block.1.weight"),
            "0.2 should become 0.1"
        );
        assert!(
            mapped
                .iter()
                .any(|v| v.name == "feature.layers.1.conv_block.0.weight"),
            "2.0 should become 1.0"
        );
        assert!(
            mapped
                .iter()
                .any(|v| v.name == "feature.layers.1.conv_block.1.weight"),
            "2.2 should become 1.1"
        );

        // Verify specific transformations
        let t1 = transformations
            .iter()
            .find(|(_, old)| old == "feature.layers.2.conv_block.2.weight");
        assert_eq!(
            t1.map(|(new, _)| new.as_str()),
            Some("feature.layers.1.conv_block.1.weight"),
            "2.2 should map to 1.1"
        );
    }

    #[test]
    fn test_map_indices_contiguous_deeply_nested() {
        // Test with three levels of nesting
        let tensors = vec![
            create_test_tensor("a.0.b.0.c.0.weight"),
            create_test_tensor("a.0.b.0.c.2.weight"),
            create_test_tensor("a.0.b.2.c.0.weight"),
            create_test_tensor("a.2.b.0.c.0.weight"),
        ];

        let (mapped, _) = map_indices_contiguous(tensors);

        // a: 0, 2 -> 0, 1
        // a.0.b: 0, 2 -> 0, 1
        // a.2.b: 0 -> 0
        // a.0.b.0.c: 0, 2 -> 0, 1
        // a.0.b.2.c: 0 -> 0
        // a.2.b.0.c: 0 -> 0

        assert!(mapped.iter().any(|v| v.name == "a.0.b.0.c.0.weight"));
        assert!(
            mapped.iter().any(|v| v.name == "a.0.b.0.c.1.weight"),
            "a.0.b.0.c.2 should become a.0.b.0.c.1"
        );
        assert!(
            mapped.iter().any(|v| v.name == "a.0.b.1.c.0.weight"),
            "a.0.b.2.c.0 should become a.0.b.1.c.0"
        );
        assert!(
            mapped.iter().any(|v| v.name == "a.1.b.0.c.0.weight"),
            "a.2.b.0.c.0 should become a.1.b.0.c.0"
        );
    }
}
