"""Apply the reference implementation to our separate clean construction checkout."""
from pathlib import Path

root = Path(__file__).resolve().parents[2] / "cache/burn-scoped-checkpoint-remap-work/crates/burn-store/src"
path = root / "keyremapper.rs"
text = path.read_text()
marker = "/// Map tensor paths to have contiguous numeric indices."
policy = '''/// Policy for renumbering numeric path segments by their original prefix.
///
/// Rules match the prefix before a numeric segment, without its trailing dot.
/// The last matching rule wins. An unmatched prefix uses the configured default.
#[derive(Debug, Clone)]
pub struct IndexMappingPolicy {
    default_map: bool,
    rules: Vec<(Regex, bool)>,
}

impl IndexMappingPolicy {
    /// Create a policy with the given fallback behavior.
    pub fn new(default_map: bool) -> Self {
        Self { default_map, rules: Vec::new() }
    }

    /// Add a regex rule. Invalid regex syntax returns an error.
    pub fn with_rule(mut self, prefix_pattern: impl AsRef<str>, map: bool) -> Result<Self, regex::Error> {
        self.rules.push((Regex::new(prefix_pattern.as_ref())?, map));
        Ok(self)
    }

    /// Decide whether to renumber the indices immediately under a prefix.
    pub fn should_map(&self, prefix: &str) -> bool {
        self.rules.iter().rev().find(|(pattern, _)| pattern.is_match(prefix))
            .map(|(_, map)| *map).unwrap_or(self.default_map)
    }
}

'''
assert text.count(marker) == 1
text = text.replace(marker, policy + marker)
signature = '''pub fn map_indices_contiguous(
    tensors: Vec<PackTensor>,
) -> (Vec<PackTensor>, Vec<(String, String)>) {'''
replacement = signature + '''
    map_indices_contiguous_with_policy(tensors, &IndexMappingPolicy::new(true))
}

/// Renumber selected numeric prefixes while preserving tensor values and order.
///
/// Prefix matching uses the input names, before any numeric segment is changed.
/// Store callers apply explicit key-remapping patterns before this stage.
pub fn map_indices_contiguous_with_policy(
    tensors: Vec<PackTensor>,
    policy: &IndexMappingPolicy,
) -> (Vec<PackTensor>, Vec<(String, String)>) {'''
assert text.count(signature) == 1
text = text.replace(signature, replacement)
needle = '''                index_maps
                    .entry(prefix)'''
assert text.count(needle) == 1
text = text.replace(needle, '''                if !policy.should_map(prefix.strip_suffix('.').unwrap_or(&prefix)) {
                    continue;
                }

''' + needle)
path.write_text(text)

path = root / "lib.rs"
text = path.read_text().replace("pub use keyremapper::{KeyRemapper, map_indices_contiguous};",
    "pub use keyremapper::{IndexMappingPolicy, KeyRemapper, map_indices_contiguous, map_indices_contiguous_with_policy};")
path.write_text(text)

path = root / "pytorch/store.rs"
text = path.read_text().replace("    map_indices_contiguous,", "    IndexMappingPolicy, map_indices_contiguous, map_indices_contiguous_with_policy,")
text = text.replace("    pub(crate) map_indices_contiguous: bool,", "    pub(crate) map_indices_contiguous: bool,\n    index_mapping_policy: Option<IndexMappingPolicy>,")
text = text.replace("            map_indices_contiguous: true,", "            map_indices_contiguous: true,\n            index_mapping_policy: None,")
text = text.replace("        self.map_indices_contiguous = map;", "        self.map_indices_contiguous = map;\n        self.index_mapping_policy = None;\n        self.tensors_cache = None;")
marker = "    /// Enable or disable automatic contiguous mapping"
position = text.index("    pub fn map_indices_contiguous(")
# Insert after existing builder method, avoiding its existing documentation.
end = text.index("\n    }", position) + len("\n    }")
text = text[:end] + '''

    /// Set a prefix-scoped index policy. This replaces the global boolean setting.
    /// Explicit key remapping runs first; numeric prefix rules use those input names.
    pub fn with_index_mapping_policy(mut self, policy: IndexMappingPolicy) -> Self {
        self.index_mapping_policy = Some(policy);
        self.tensors_cache = None;
        self
    }
''' + text[end:]
text = text.replace("        if self.map_indices_contiguous {", "        if let Some(policy) = &self.index_mapping_policy {\n            let (mapped, _) = map_indices_contiguous_with_policy(tensors, policy);\n            tensors = mapped;\n        } else if self.map_indices_contiguous {")
path.write_text(text)

path = root / "safetensors/store.rs"
text = path.read_text().replace("use crate::{KeyRemapper, map_indices_contiguous};", "use crate::{IndexMappingPolicy, KeyRemapper, map_indices_contiguous, map_indices_contiguous_with_policy};")
text = text.replace("    map_indices_contiguous: bool,", '    map_indices_contiguous: bool,\n    #[cfg(feature = "std")]\n    index_mapping_policy: Option<IndexMappingPolicy>,')
text = text.replace("            map_indices_contiguous: false,", '            map_indices_contiguous: false,\n            #[cfg(feature = "std")]\n            index_mapping_policy: None,')
text = text.replace("            Self::File(p) => p.map_indices_contiguous = map,", "            Self::File(p) => { p.map_indices_contiguous = map; p.index_mapping_policy = None; p.tensors_cache = None; },")
text = text.replace("            Self::Memory(p) => p.map_indices_contiguous = map,", "            Self::Memory(p) => { p.map_indices_contiguous = map; p.index_mapping_policy = None; p.tensors_cache = None; },")
position = text.index("    pub fn map_indices_contiguous(")
end = text.index("\n    }", position) + len("\n    }")
text = text[:end] + '''

    /// Set a prefix-scoped index policy for loading. Replaces the global setting.
    /// Explicit key remapping runs first; numeric prefix rules use those input names.
    #[cfg(feature = "std")]
    pub fn with_index_mapping_policy(mut self, policy: IndexMappingPolicy) -> Self {
        match &mut self {
            Self::File(p) => { p.index_mapping_policy = Some(policy); p.tensors_cache = None; },
            Self::Memory(p) => { p.index_mapping_policy = Some(policy); p.tensors_cache = None; },
        }
        self
    }
''' + text[end:]
text = text.replace('''        if self.get_map_indices_contiguous() {
            let (mapped, _) = map_indices_contiguous(tensors);
            tensors = mapped;
        }''', '''        {
            let policy = match self {
                Self::File(p) => p.index_mapping_policy.as_ref(),
                Self::Memory(p) => p.index_mapping_policy.as_ref(),
            };
            if let Some(policy) = policy {
                let (mapped, _) = map_indices_contiguous_with_policy(tensors, policy);
                tensors = mapped;
            } else if self.get_map_indices_contiguous() {
                let (mapped, _) = map_indices_contiguous(tensors);
                tensors = mapped;
            }
        }''')
path.write_text(text)
print("Applied reference changes to", root)
