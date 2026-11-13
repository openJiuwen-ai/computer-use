// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::*;

#[derive(Clone, Copy)]
struct Item<'a> {
    id: NodeId,
    key: &'a str,
    box_: &'a Box,
}

struct Row<'a> {
    items: Vec<Item<'a>>,
    box_: Box,
}

fn compare(left: f64, right: f64) -> Ordering {
    left.partial_cmp(&right).unwrap_or(Ordering::Equal)
}

fn median(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut values: Vec<_> = values.into_iter().collect();
    values.sort_by(|left, right| compare(*left, *right));
    let middle = values.len() / 2;
    if values.len() % 2 == 0 { (values[middle - 1] + values[middle]) / 2.0 } else { values[middle] }
}

fn horizontal(items: &mut [Item<'_>]) {
    items.sort_by(|left, right| {
        compare(left.box_.x, right.box_.x)
            .then_with(|| compare(left.box_.center().0, right.box_.center().0))
            .then_with(|| left.key.cmp(right.key))
    });
}

fn row_compatible(items: &[Item<'_>]) -> bool {
    let smallest = items.iter().map(|item| item.box_.height).fold(f64::INFINITY, f64::min);
    if items.iter().any(|item| item.box_.height > 3.0 * smallest) {
        return false;
    }
    let top = items.iter().map(|item| item.box_.y).fold(f64::NEG_INFINITY, f64::max);
    let bottom = items.iter().map(|item| item.box_.bottom()).fold(f64::INFINITY, f64::min);
    let low = items.iter().map(|item| item.box_.center().1).fold(f64::INFINITY, f64::min);
    let high = items.iter().map(|item| item.box_.center().1).fold(f64::NEG_INFINITY, f64::max);
    if bottom - top < 0.6 * smallest || high - low > 0.5 * smallest {
        return false;
    }
    let mut ordered = items.to_vec();
    horizontal(&mut ordered);
    ordered.windows(2).all(|pair| {
        let (left, right) = (pair[0].box_, pair[1].box_);
        let width = left.width.min(right.width);
        right.x > left.x && right.center().0 > left.center().0
            && !left.contains(right) && !right.contains(left)
            && left.right() - right.x <= 0.35 * width
            && right.center().0 - left.center().0 >= 0.5 * width
    })
}

fn union(items: &[Item<'_>]) -> Result<Box> {
    let left = items.iter().map(|item| item.box_.x).fold(f64::INFINITY, f64::min);
    let top = items.iter().map(|item| item.box_.y).fold(f64::INFINITY, f64::min);
    let right = items.iter().map(|item| item.box_.right()).fold(f64::NEG_INFINITY, f64::max);
    let bottom = items.iter().map(|item| item.box_.bottom()).fold(f64::NEG_INFINITY, f64::max);
    let union = Box { space: items[0].box_.space.clone(), x: left, y: top, width: right - left, height: bottom - top };
    if union.width <= 0.0 || union.height <= 0.0 || union.area() <= 0.0
        || ![union.right(), union.bottom(), union.area()].into_iter().all(f64::is_finite)
    {
        return Err(ProjectionError::Invalid("invalid spatial row bounds"));
    }
    Ok(union)
}

fn item_keys<'a, 'b>(items: &'b [Item<'a>]) -> impl Iterator<Item = &'a str> + 'b {
    items.iter().map(|item| item.key)
}

fn form_rows<'a>(items: &[Item<'a>]) -> Result<Vec<Row<'a>>> {
    let mut ordered = items.to_vec();
    ordered.sort_by(|left, right| {
        compare(left.box_.center().1, right.box_.center().1)
            .then_with(|| compare(left.box_.y, right.box_.y))
            .then_with(|| compare(left.box_.x, right.box_.x))
            .then_with(|| left.key.cmp(right.key))
    });
    let mut groups: Vec<Vec<Item<'_>>> = Vec::new();
    for item in ordered {
        let mut best: Option<(usize, f64, Vec<&str>)> = None;
        for (index, group) in groups.iter_mut().enumerate() {
            group.push(item);
            let compatible = row_compatible(group);
            group.pop();
            if !compatible {
                continue;
            }
            let distance = (item.box_.center().1 - median(group.iter().map(|item| item.box_.center().1))).abs();
            if best.as_ref().is_some_and(|(_, best_distance, _)| distance > *best_distance) {
                continue;
            }
            let mut ordered = group.clone();
            horizontal(&mut ordered);
            let keys: Vec<_> = item_keys(&ordered).collect();
            if best.as_ref().is_none_or(|(_, prior_distance, prior_keys)| {
                compare(distance, *prior_distance).then_with(|| keys.cmp(prior_keys)).is_lt()
            }) {
                best = Some((index, distance, keys));
            }
        }
        if let Some((index, _, _)) = best {
            groups[index].push(item);
        } else {
            groups.push(vec![item]);
        }
    }
    let mut rows = Vec::new();
    for mut items in groups {
        horizontal(&mut items);
        rows.push(Row { box_: union(&items)?, items });
    }
    rows.sort_by(|left, right| {
        compare(left.box_.center().1, right.box_.center().1)
            .then_with(|| compare(left.box_.y, right.box_.y))
            .then_with(|| compare(left.box_.x, right.box_.x))
            .then_with(|| item_keys(&left.items).cmp(item_keys(&right.items)))
    });
    Ok(rows)
}

#[derive(Clone, Copy)]
enum Anchor {
    Left,
    Right,
    Center,
}

impl Anchor {
    fn coordinate(self, box_: &Box) -> f64 {
        match self {
            Self::Left => box_.x,
            Self::Right => box_.right(),
            Self::Center => box_.center().0,
        }
    }
}

fn anchor_groups<'a>(items: &[Item<'a>], kind: Anchor, scale: f64) -> Vec<Vec<Item<'a>>> {
    let mut ordered = items.to_vec();
    ordered.sort_by(|left, right| {
        compare(kind.coordinate(left.box_), kind.coordinate(right.box_))
            .then_with(|| compare(left.box_.y, right.box_.y))
            .then_with(|| left.key.cmp(right.key))
    });
    let mut groups: Vec<Vec<Item<'_>>> = Vec::new();
    for item in ordered {
        if let Some(group) = groups.last_mut() {
            let size = group.iter().map(|member| member.box_.height).fold(scale.min(item.box_.height), f64::min);
            if kind.coordinate(item.box_) - kind.coordinate(group[0].box_) <= 0.25 * size {
                group.push(item);
                continue;
            }
        }
        groups.push(vec![item]);
    }
    groups
}

fn unambiguous_members<'a>(group: &[Item<'a>], row_of: &HashMap<NodeId, usize>) -> Vec<Item<'a>> {
    let mut counts = HashMap::new();
    for item in group {
        *counts.entry(row_of[&item.id]).or_insert(0) += 1;
    }
    let mut candidates: Vec<_> = group.iter().copied().filter(|item| counts[&row_of[&item.id]] == 1).collect();
    candidates.sort_by(|left, right| compare(left.box_.y, right.box_.y).then_with(|| left.key.cmp(right.key)));
    let mut active: Vec<Item<'_>> = Vec::new();
    let mut conflicting = HashSet::new();
    for item in &candidates {
        active.retain(|previous| previous.box_.bottom() > item.box_.y);
        for previous in &active {
            let overlap = previous.box_.bottom().min(item.box_.bottom()) - item.box_.y;
            if overlap > 0.25 * previous.box_.height.min(item.box_.height) {
                conflicting.extend([previous.id, item.id]);
            }
        }
        active.push(*item);
    }
    candidates.retain(|item| !conflicting.contains(&item.id));
    candidates.sort_by(|left, right| row_of[&left.id].cmp(&row_of[&right.id])
        .then_with(|| compare(left.box_.y, right.box_.y)).then_with(|| left.key.cmp(right.key)));
    candidates
}

fn anchors(items: &[Item<'_>], rows: &[Row<'_>]) -> Vec<Vec<NodeId>> {
    let row_of: HashMap<_, _> = rows.iter().enumerate().flat_map(|(index, row)| {
        row.items.iter().map(move |item| (item.id, index))
    }).collect();
    let scale = median(items.iter().map(|item| item.box_.height));
    let mut anchors = Vec::new();
    for kind in [Anchor::Left, Anchor::Right, Anchor::Center] {
        for group in anchor_groups(items, kind, scale) {
            let members = unambiguous_members(&group, &row_of);
            if members.len() < 2 {
                continue;
            }
            let position = median(members.iter().map(|item| kind.coordinate(item.box_)));
            let supporting: HashSet<_> = members.iter().map(|item| row_of[&item.id]).collect();
            let mut intervals: Vec<_> = items.iter().filter(|item| {
                !supporting.contains(&row_of[&item.id]) && item.box_.x < position && position < item.box_.right()
            }).map(|item| (item.box_.y, item.box_.bottom())).collect();
            intervals.sort_by(|left, right| compare(left.0, right.0).then_with(|| compare(left.1, right.1)));
            let mut high = f64::NEG_INFINITY;
            for (_, bottom) in &mut intervals {
                high = high.max(*bottom);
                *bottom = high;
            }
            let mut run = Vec::new();
            for (index, item) in members.iter().enumerate() {
                if index != 0 {
                    let before = members[index - 1].box_;
                    let after = item.box_;
                    let overlap = before.bottom().min(after.bottom()) - before.y.max(after.y);
                    let corridor = intervals.partition_point(|interval| interval.0 < after.y);
                    let blocked = after.y > before.bottom() && corridor != 0 && intervals[corridor - 1].1 > before.bottom();
                    if overlap > 0.25 * before.height.min(after.height) || blocked {
                        if run.len() >= 2 {
                            anchors.push(std::mem::take(&mut run));
                        } else {
                            run.clear();
                        }
                    }
                }
                run.push(item.id);
            }
            if run.len() >= 2 {
                anchors.push(run);
            }
        }
    }
    anchors
}

pub(crate) fn atomic_rows(
    projection: &Projection, children: &[NodeId], geometry: &Geometry, minimum_rows: usize,
) -> Result<Vec<Vec<NodeId>>> {
    if minimum_rows == 0 || children.len() < minimum_rows * 2 {
        return Ok(Vec::new());
    }
    let mut items = Vec::new();
    let mut seen = HashSet::new();
    for &id in children {
        let node = projection.display_node(id)?;
        if !complete_visible(&node.fields, &node.children)? || logical_structure(&node.fields)
            || !node.children.is_empty() || !seen.insert(id)
        {
            return Ok(Vec::new());
        }
        let Some(box_) = geometry.get(&id) else { return Ok(Vec::new()) };
        items.push(Item { id, key: &node.reference, box_ });
    }
    if items.iter().any(|item| !item.box_.comparable(items[0].box_)) {
        return Ok(Vec::new());
    }
    let rows = form_rows(&items)?;
    if rows.len() < minimum_rows || rows.iter().any(|row| row.items.len() < 2 || row.items.len() != rows[0].items.len())
        || rows.windows(2).any(|rows| rows[0].box_.bottom() > rows[1].box_.y)
    {
        return Ok(Vec::new());
    }
    for row in &rows {
        let roles: Vec<_> = row.items.iter().map(|item| projection.display_node(item.id).map(|node| role(&node.fields)))
            .collect::<Result<Vec<_>>>()?;
        if !roles.iter().any(|role| matches!(*role, "staticText" | "heading"))
            || !roles.into_iter().any(contained_caption)
        {
            return Ok(Vec::new());
        }
    }
    if rows.len() > 1 {
        let anchors = anchors(&items, &rows);
        if (0..rows[0].items.len()).any(|column| {
            !anchors.iter().any(|anchor| rows.iter().all(|row| anchor.contains(&row.items[column].id)))
        }) {
            return Ok(Vec::new());
        }
    }
    Ok(rows.into_iter().map(|row| row.items.into_iter().map(|item| item.id).collect()).collect())
}

fn repeated_rows(projection: &Projection, children: &[NodeId], geometry: &Geometry) -> Result<Vec<Vec<NodeId>>> {
    for &id in children {
        let role = role(&projection.display_node(id)?.fields);
        if !contained_caption(role) && !matches!(role, "staticText" | "heading" | "image") {
            return Ok(Vec::new());
        }
    }
    atomic_rows(projection, children, geometry, 2)
}

pub(super) fn group_rows(projection: &mut Projection, geometry: &Geometry) -> Result<()> {
    let mut pending: Vec<_> = projection.roots.iter().rev().copied().collect();
    while let Some(id) = pending.pop() {
        let parent = projection.display_node(id)?;
        let mut accepted = available_region(parent)? && visible_source_path(projection, id)?
            && !native_path_matches(projection, id, logical_structure)?;
        if accepted {
            for &child in &parent.children {
                let child = projection.display_node(child)?;
                if child.scope != parent.scope || !same_blocking(parent, child)? {
                    accepted = false;
                    break;
                }
            }
        }
        let mut groups = if accepted { repeated_rows(projection, &parent.children, geometry)? } else { Vec::new() };
        if !groups.is_empty() {
            let mut partitions = HashMap::new();
            for &child in &parent.children {
                partitions.insert(child, source_region(projection, id, child)?);
            }
            if partitions.values().any(Option::is_none) || groups.iter().any(|group| {
                group.iter().any(|child| partitions[child] != partitions[&group[0]])
            }) {
                groups.clear();
            }
        }
        if !groups.is_empty() {
            let mut layout = Vec::new();
            for group in &groups {
                let members = group.iter().map(|&member| reference(projection, member)).collect::<Result<Vec<_>>>()?;
                layout.push(Value::Map(vec![
                    ("kind".to_owned(), Value::Text("row".to_owned())),
                    ("members".to_owned(), Value::Seq(members)),
                ]));
            }
            let layout = Value::Seq(layout);
            annotate(projection, id, "layoutGroups", layout.clone())?;
            projection.node_mut(id)?.children = groups.iter().flatten().copied().collect();
            for group in groups {
                let mut captions = Vec::new();
                for &member in &group {
                    let node = projection.display_node(member)?;
                    if matches!(role(&node.fields), "staticText" | "heading") {
                        captions.push(Value::Text(node.reference.clone()));
                    }
                }
                for member in group {
                    add_context(projection, member, &captions)?;
                }
            }
            event(projection, id, "spatial-rows", [("groups", layout)])?;
        }
        pending.extend(projection.display_node(id)?.children.iter().rev().copied());
    }
    Ok(())
}
