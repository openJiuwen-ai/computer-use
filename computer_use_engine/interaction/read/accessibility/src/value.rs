// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Ordered observation data with ownership independent of the Python interpreter.

use std::ops::Deref;

pub(super) mod decimal;
pub(super) mod diagnostic;

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Value {
    Null,
    Bool(bool),
    Int(i64),
    BigInt(String),
    Float(f64),
    Text(String),
    Bytes(Vec<u8>),
    Seq(Vec<Value>),
    Map(Vec<(String, Value)>),
}

impl Value {
    pub(crate) fn get(&self, name: &str) -> Option<&Self> {
        self.as_map()?.iter().find(|(key, _)| key == name).map(|(_, value)| value)
    }

    pub(crate) fn get_mut(&mut self, name: &str) -> Option<&mut Self> {
        let Self::Map(items) = self else { return None };
        items.iter_mut().find(|(key, _)| key == name).map(|(_, value)| value)
    }

    pub(crate) fn as_bool(&self) -> Option<bool> {
        match self {
            Self::Bool(value) => Some(*value),
            _ => None,
        }
    }

    pub(crate) fn as_i64(&self) -> Option<i64> {
        match self {
            Self::Int(value) => Some(*value),
            _ => None,
        }
    }

    pub(crate) fn as_i128(&self) -> Option<i128> {
        match self {
            Self::Int(value) => Some(i128::from(*value)),
            Self::BigInt(value) => value.parse().ok(),
            _ => None,
        }
    }

    pub(crate) fn from_i128(value: i128) -> Self {
        i64::try_from(value).map(Self::Int).unwrap_or_else(|_| Self::BigInt(value.to_string()))
    }

    pub(crate) fn as_str(&self) -> Option<&str> {
        match self {
            Self::Text(value) => Some(value),
            _ => None,
        }
    }

    pub(crate) fn as_seq(&self) -> Option<&[Self]> {
        match self {
            Self::Seq(items) => Some(items),
            _ => None,
        }
    }

    pub(crate) fn as_map(&self) -> Option<&[(String, Self)]> {
        match self {
            Self::Map(items) => Some(items),
            _ => None,
        }
    }
}

/// The numbered semantic roots follow AX containment. Release them without
/// putting one destructor frame per ancestor on the SDK worker stack.
pub(crate) struct SemanticTree(Value);

impl From<Value> for SemanticTree {
    fn from(value: Value) -> Self { Self(value) }
}

impl Deref for SemanticTree {
    type Target = Value;

    fn deref(&self) -> &Value { &self.0 }
}

impl SemanticTree {
    #[cfg(test)]
    pub(crate) fn get_mut(&mut self, name: &str) -> Option<&mut Value> { self.0.get_mut(name) }
}

impl Drop for SemanticTree {
    fn drop(&mut self) {
        let Some(Value::Seq(roots)) = self.0.get_mut("roots") else { return; };
        let mut pending = std::mem::take(roots);
        while let Some(mut node) = pending.pop() {
            if let Some(Value::Seq(children)) = node.get_mut("children") {
                pending.append(children);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deep_semantic_tree_releases_on_an_sdk_worker_stack() {
        std::thread::Builder::new().stack_size(512 * 1024).spawn(|| {
            let mut root = Value::Null;
            for _ in 0..20_000 {
                root = Value::Map(vec![("children".to_owned(), Value::Seq(vec![root]))]);
            }
            drop(SemanticTree::from(Value::Map(vec![("roots".to_owned(), Value::Seq(vec![root]))])));
        }).unwrap().join().unwrap();
    }
}
