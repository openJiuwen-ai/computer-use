// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Comparable read geometry. Missing bounds do not remove a native target.

use std::collections::{HashMap, HashSet};

use super::{numbering::NodeId, value::Value};

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Box {
    pub(crate) space: String,
    pub(crate) x: f64,
    pub(crate) y: f64,
    pub(crate) width: f64,
    pub(crate) height: f64,
}

impl Box {
    pub(crate) fn right(&self) -> f64 { self.x + self.width }
    pub(crate) fn bottom(&self) -> f64 { self.y + self.height }
    pub(crate) fn area(&self) -> f64 { self.width * self.height }
    pub(crate) fn center(&self) -> (f64, f64) { (self.x + self.width / 2.0, self.y + self.height / 2.0) }

    pub(crate) fn comparable(&self, other: &Self) -> bool { self.space == other.space }

    pub(crate) fn contains(&self, other: &Self) -> bool {
        self.comparable(other) && self.x <= other.x && self.y <= other.y
            && self.right() >= other.right() && self.bottom() >= other.bottom()
    }

    pub(crate) fn intersection_area(&self, other: &Self) -> f64 {
        if !self.comparable(other) { return 0.0; }
        (self.right().min(other.right()) - self.x.max(other.x)).max(0.0)
            * (self.bottom().min(other.bottom()) - self.y.max(other.y)).max(0.0)
    }

    pub(crate) fn overlap(&self, other: &Self) -> f64 {
        self.intersection_area(other) / self.area().min(other.area())
    }

    pub(crate) fn horizontal_overlap(&self, other: &Self) -> f64 {
        if !self.comparable(other) { return 0.0; }
        (self.right().min(other.right()) - self.x.max(other.x)).max(0.0) / self.width.min(other.width)
    }

    pub(crate) fn vertical_overlap(&self, other: &Self) -> f64 {
        if !self.comparable(other) { return 0.0; }
        (self.bottom().min(other.bottom()) - self.y.max(other.y)).max(0.0) / self.height.min(other.height)
    }
}

type Rect = [f64; 4];

#[derive(Default)]
struct Restrictions<'a> {
    all: bool,
    all_nodes: bool,
    all_spaces: bool,
    nodes: HashSet<&'a str>,
    spaces: HashSet<&'a str>,
    space_fields: HashMap<&'a str, HashSet<&'a str>>,
}

impl<'a> Restrictions<'a> {
    fn collect(issues: &'a [Value]) -> Self {
        let mut restrictions = Self::default();
        for issue in issues {
            let Some(path) = issue.get("path").and_then(Value::as_seq) else { continue };
            let mut names = path.iter().map(Value::as_str);
            match (names.next().flatten(), names.next().flatten()) {
                (None, _) if path.is_empty() => restrictions.all = true,
                (Some("nodes"), None) if path.len() == 1 => {
                    if issue.get("state").and_then(Value::as_str) != Some("partial") {
                        restrictions.all_nodes = true;
                    }
                }
                (Some("nodes"), Some(token)) => match (path.len(), names.next().flatten(), names.next().flatten()) {
                    (2, _, _) | (3, Some("geometry"), _) | (_, Some("geometry"), Some("bounds")) => {
                        restrictions.nodes.insert(token);
                    }
                    _ => {}
                },
                (Some("coordinate_spaces"), None) if path.len() == 1 => restrictions.all_spaces = true,
                (Some("coordinate_spaces"), Some(space)) if path.len() == 2 => {
                    restrictions.spaces.insert(space);
                }
                (Some("coordinate_spaces"), Some(space)) => {
                    if let Some(field) = names.next().flatten() {
                        restrictions.space_fields.entry(space).or_default().insert(field);
                    }
                }
                _ => {}
            }
        }
        restrictions
    }

    fn affects_space(&self, id: &str, fields: &[&str]) -> bool {
        self.all || self.all_spaces || self.spaces.contains(id)
            || self.space_fields.get(id).is_some_and(|blocked| fields.iter().any(|name| blocked.contains(name)))
    }
}

fn number(value: &Value) -> Option<f64> {
    let result = match value {
        Value::Int(value) => *value as f64,
        Value::BigInt(value) => value.parse().ok()?,
        Value::Float(value) => *value,
        _ => return None,
    };
    result.is_finite().then_some(result)
}

fn text<'a>(row: &'a Value, name: &str) -> Option<&'a str> { row.get(name).and_then(Value::as_str) }

fn valid_rect([x, y, width, height]: Rect) -> bool {
    width > 0.0 && height > 0.0 && width * height > 0.0
        && (x + width).is_finite() && (y + height).is_finite() && (width * height).is_finite()
}

fn bounds(geometry: &Value) -> Option<Rect> {
    if geometry.get("valid").and_then(Value::as_bool) == Some(false) { return None; }
    let rect = ["x", "y", "width", "height"].map(|key| geometry.get(key).and_then(number));
    let [Some(x), Some(y), Some(w), Some(h)] = rect else { return None };
    valid_rect([x, y, w, h]).then_some([x, y, w, h])
}

fn transformed([x, y, width, height]: Rect, matrix: &Value) -> Option<Rect> {
    let matrix: &[Value; 6] = matrix.as_seq()?.try_into().ok()?;
    let [Some(a), Some(b), Some(c), Some(d), Some(tx), Some(ty)] = std::array::from_fn(|index| number(&matrix[index]))
        else { return None };
    if !((b == 0.0 && c == 0.0 && a != 0.0 && d != 0.0) || (a == 0.0 && d == 0.0 && b != 0.0 && c != 0.0)) {
        return None;
    }
    let points = [(x, y), (x + width, y), (x, y + height), (x + width, y + height)]
        .map(|(px, py)| (a * px + c * py + tx, b * px + d * py + ty));
    let (mut left, mut top) = points[0];
    let (mut right, mut bottom) = points[0];
    for (px, py) in points {
        if !px.is_finite() || !py.is_finite() { return None; }
        left = left.min(px);
        top = top.min(py);
        right = right.max(px);
        bottom = bottom.max(py);
    }
    let rect = [left, top, right - left, bottom - top];
    valid_rect(rect).then_some(rect)
}

fn usable_space<'a>(id: &str, spaces: &'a Value, issues: &Restrictions<'_>) -> Option<&'a Value> {
    let space = spaces.get(id)?;
    (matches!(text(space, "y_direction"), Some("up" | "down"))
        && !issues.affects_space(id, &["unit", "origin", "owner", "y_direction"])).then_some(space)
}

fn comparable_box(geometry: &Value, spaces: &Value, issues: &Restrictions<'_>) -> Option<Box> {
    let mut rect = bounds(geometry)?;
    let mut id = text(geometry, "space")?;
    let mut space = usable_space(id, spaces, issues)?;
    let mut visited = HashSet::from([id]);
    loop {
        let parent = text(space, "parent");
        let transform = space.get("to_parent").filter(|value| !matches!(value, Value::Null));
        match (parent, transform) {
            (None, None) => break,
            (Some(parent), Some(matrix)) => {
                if issues.affects_space(id, &["parent", "to_parent"]) || !visited.insert(parent) { return None; }
                space = usable_space(parent, spaces, issues)?;
                rect = transformed(rect, matrix)?;
                id = parent;
            }
            _ => return None,
        }
    }
    let [x, mut y, width, height] = rect;
    if text(space, "y_direction") == Some("up") { y = -(y + height); }
    valid_rect([x, y, width, height]).then_some(Box { space: id.to_owned(), x, y, width, height })
}

pub(crate) type Geometry = HashMap<NodeId, Box>;

pub(crate) fn collect_geometry(observation: &Value) -> Geometry {
    let Some(nodes) = observation.get("nodes").and_then(Value::as_map) else { return HashMap::new(); };
    let Some(spaces) = observation.get("coordinate_spaces") else { return HashMap::new(); };
    let issues = Restrictions::collect(observation.get("issues").and_then(Value::as_seq).unwrap_or_default());
    if issues.all || issues.all_nodes || issues.all_spaces { return HashMap::new(); }

    let ordinals: HashMap<_, _> = nodes.iter().enumerate().map(|(i, (token, _))| (token.as_str(), i)).collect();
    let mut excluded = vec![false; nodes.len()];
    let mut pending = Vec::new();
    for (index, (_, node)) in nodes.iter().enumerate() {
        let flags = node.get("states");
        let flag = |key| flags.and_then(|row| row.get(key)).and_then(Value::as_bool);
        if flag("offscreen") == Some(true) || flag("visible") == Some(false) || flag("showing") == Some(false) {
            excluded[index] = true;
            pending.push(index);
        }
    }
    while let Some(index) = pending.pop() {
        let Some(relations) = nodes[index].1.get("relations").and_then(Value::as_map) else { continue };
        for (_, relation) in relations {
            if text(relation, "kind") != Some("children") || text(relation, "evidence") == Some("candidate") { continue; }
            for target in relation.get("targets").and_then(Value::as_seq).unwrap_or_default() {
                if let Some(&child) = target.as_str().and_then(|token| ordinals.get(token)) {
                    if !excluded[child] {
                        excluded[child] = true;
                        pending.push(child);
                    }
                }
            }
        }
    }
    nodes.iter().enumerate().filter_map(|(index, (token, node))| {
        if excluded[index] || issues.nodes.contains(token.as_str()) { return None; }
        let bounds = node.get("geometry")?.get("bounds")?;
        comparable_box(bounds, spaces, &issues).map(|rect| (NodeId(index), rect))
    }).collect()
}

/// The caller selects this cue only for the compact public macOS window view.
pub(crate) fn outside_window_fields(observation: &Value, geometry: &Geometry, root: NodeId) -> HashSet<NodeId> {
    let Some(nodes) = observation.get("nodes").and_then(Value::as_map) else { return HashSet::new(); };
    let (Some((token, node)), Some(frame)) = (nodes.get(root.0), geometry.get(&root)) else { return HashSet::new(); };
    let Some([root_token]) = observation.get("roots").and_then(Value::as_seq) else { return HashSet::new(); };
    if root_token.as_str() != Some(token) || text(node, "role") != Some("window") { return HashSet::new(); }
    let Some(children) = node.get("relations").and_then(|relations| relations.get("children")) else {
        return HashSet::new();
    };
    if text(children, "evidence") == Some("candidate") { return HashSet::new(); }
    let direct: HashSet<_> = children.get("targets").and_then(Value::as_seq).unwrap_or_default()
        .iter().filter_map(Value::as_str).collect();

    nodes.iter().enumerate().filter_map(|(index, (name, node))| {
        let id = NodeId(index);
        let bounds = geometry.get(&id)?;
        if !direct.contains(name.as_str()) || !bounds.comparable(frame) || text(node, "role") != Some("text_field")
            || node.get("states")?.get("enabled")?.as_bool() != Some(true)
        {
            return None;
        }
        let relations = node.get("relations")?;
        let parent = relations.get("parent")?;
        let [parent_target] = parent.get("targets")?.as_seq()? else { return None; };
        if text(parent, "evidence") == Some("candidate") || parent_target != root_token
            || parent.get("total").and_then(Value::as_i64) != Some(1)
        {
            return None;
        }
        if relations.get("window").and_then(|relation| relation.get("targets")).and_then(Value::as_seq)
            .is_some_and(|targets| targets.iter().any(|owner| owner != root_token))
        {
            return None;
        }
        (bounds.x.max(frame.x) > bounds.right().min(frame.right())
            || bounds.y.max(frame.y) > bounds.bottom().min(frame.bottom())).then_some(id)
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    fn json(source: &str) -> Value { decode_json(source.as_bytes()).unwrap() }

    #[test]
    fn applies_declared_axis_swaps_then_the_root_direction() {
        let rect = json(r#"{"space":"local","x":2,"y":4,"width":8,"height":5}"#);
        let spaces = json(r#"{
            "local":{"parent":"root","to_parent":[0,2,-3,0,1,5],"y_direction":"down"},
            "root":{"y_direction":"up"}
        }"#);
        let bounds = comparable_box(&rect, &spaces, &Restrictions::default()).unwrap();
        assert_eq!(bounds, Box { space: "root".into(), x: -26.0, y: -25.0, width: 15.0, height: 16.0 });
        let blocked = json(r#"[{"path":["coordinate_spaces","local","to_parent",1],"state":"absent"}]"#);
        assert_eq!(comparable_box(&rect, &spaces, &Restrictions::collect(blocked.as_seq().unwrap())), None);
    }

    #[test]
    fn partial_collections_keep_admitted_bounds_and_hidden_ancestors_exclude_descendants() {
        let capture = json(r#"{
            "coordinate_spaces":{"s":{"y_direction":"down"}},
            "issues":[{"path":["nodes"],"state":"partial"},{"path":["nodes","first","name"],"state":"timeout"}],
            "nodes":{
                "first":{"geometry":{"bounds":{"space":"s","x":0,"y":0,"width":1,"height":1}}},
                "hidden":{"states":{"visible":false},"relations":{"children":{"kind":"children","targets":["child"]}}},
                "child":{"geometry":{"bounds":{"space":"s","x":0,"y":0,"width":1,"height":1}}}
            }
        }"#);
        let boxes = collect_geometry(&capture);
        assert_eq!(boxes.len(), 1);
        assert!(boxes.contains_key(&NodeId(0)));
    }
}
