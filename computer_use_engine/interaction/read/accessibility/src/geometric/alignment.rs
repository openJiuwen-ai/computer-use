// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};

use super::{union, BlockId, Box, GeometricPlan};

#[derive(Clone, Debug)]
pub(crate) struct LayoutItem {
    pub(crate) key: BlockId,
    pub(crate) bounds: Box,
}

#[derive(Clone, Debug)]
pub(crate) struct RowBand {
    pub(crate) members: Vec<BlockId>,
    pub(crate) bounds: Box,
}

#[derive(Clone, Debug)]
pub(crate) struct Anchor {
    pub(crate) kind: &'static str,
    pub(crate) position: f64,
    pub(crate) members: Vec<BlockId>,
    pub(crate) rows: Vec<usize>,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct Alignment {
    pub(crate) rows: Vec<RowBand>,
    pub(crate) anchors: Vec<Anchor>,
    pub(crate) scale: f64,
    pub(crate) items: Vec<LayoutItem>,
}

pub(super) fn cmp(a: f64, b: f64) -> Ordering {
    a.partial_cmp(&b).expect("layout boxes are finite")
}

pub(super) fn key_name(plan: &GeometricPlan, id: BlockId) -> &str {
    &plan.block(id).expect("layout identity exists").key
}

pub(super) fn compare_keys(plan: &GeometricPlan, a: &[BlockId], b: &[BlockId]) -> Ordering {
    a.iter()
        .map(|&key| key_name(plan, key))
        .cmp(b.iter().map(|&key| key_name(plan, key)))
}

pub(super) fn median(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut values: Vec<_> = values.into_iter().collect();
    values.sort_by(|&a, &b| cmp(a, b));
    let middle = values.len() / 2;
    if values.len() % 2 == 1 {
        values[middle]
    } else {
        (values[middle - 1] + values[middle]) / 2.0
    }
}

fn horizontal<'a>(items: &[&'a LayoutItem], plan: &GeometricPlan) -> Vec<&'a LayoutItem> {
    let mut ordered = items.to_vec();
    ordered.sort_by(|a, b| {
        cmp(a.bounds.x, b.bounds.x)
            .then_with(|| cmp(a.bounds.center().0, b.bounds.center().0))
            .then_with(|| key_name(plan, a.key).cmp(key_name(plan, b.key)))
    });
    ordered
}

fn row_compatible(items: &[&LayoutItem], plan: &GeometricPlan) -> bool {
    let smallest = items.iter().map(|item| item.bounds.height).reduce(f64::min).unwrap();
    let largest = items.iter().map(|item| item.bounds.height).reduce(f64::max).unwrap();
    if largest > 3.0 * smallest {
        return false;
    }
    let top = items.iter().map(|item| item.bounds.y).reduce(f64::max).unwrap();
    let bottom = items.iter().map(|item| item.bounds.bottom()).reduce(f64::min).unwrap();
    let low = items
        .iter()
        .map(|item| item.bounds.center().1)
        .reduce(f64::min)
        .unwrap();
    let high = items
        .iter()
        .map(|item| item.bounds.center().1)
        .reduce(f64::max)
        .unwrap();
    if bottom - top < 0.6 * smallest || high - low > 0.5 * smallest {
        return false;
    }
    horizontal(items, plan).windows(2).all(|pair| {
        let (first, second) = (&pair[0].bounds, &pair[1].bounds);
        let width = first.width.min(second.width);
        second.x > first.x
            && second.center().0 > first.center().0
            && !first.contains(second)
            && !second.contains(first)
            && first.right() - second.x <= 0.35 * width
            && second.center().0 - first.center().0 >= 0.5 * width
    })
}

pub(crate) fn same_row(items: &[LayoutItem], plan: &GeometricPlan, require_order: bool) -> bool {
    if items.is_empty()
        || items.iter().map(|item| item.key).collect::<HashSet<_>>().len() != items.len()
        || items.iter().any(|item| item.bounds.space != items[0].bounds.space)
    {
        return false;
    }
    let members: Vec<_> = items.iter().collect();
    row_compatible(&members, plan)
        && (!require_order || {
            horizontal(&members, plan)
                .iter()
                .map(|item| item.key)
                .eq(items.iter().map(|item| item.key))
        })
}

fn rows(items: &[LayoutItem], plan: &GeometricPlan) -> Vec<RowBand> {
    let mut ordered: Vec<_> = items.iter().collect();
    ordered.sort_by(|a, b| {
        cmp(a.bounds.center().1, b.bounds.center().1)
            .then_with(|| cmp(a.bounds.y, b.bounds.y))
            .then_with(|| cmp(a.bounds.x, b.bounds.x))
            .then_with(|| key_name(plan, a.key).cmp(key_name(plan, b.key)))
    });
    let mut groups = Vec::<Vec<&LayoutItem>>::new();
    for item in ordered {
        let mut selected = None;
        for (index, group) in groups.iter_mut().enumerate() {
            group.push(item);
            let compatible = row_compatible(group, plan);
            group.pop();
            if !compatible {
                continue;
            }
            let distance = (item.bounds.center().1 - median(group.iter().map(|member| member.bounds.center().1))).abs();
            let members: Vec<_> = horizontal(group, plan).iter().map(|member| member.key).collect();
            if selected
                .as_ref()
                .is_none_or(|(_, prior_distance, prior_members): &(usize, f64, Vec<BlockId>)| {
                    cmp(distance, *prior_distance)
                        .then_with(|| compare_keys(plan, &members, prior_members))
                        .is_lt()
                })
            {
                selected = Some((index, distance, members));
            }
        }
        if let Some((index, _, _)) = selected {
            groups[index].push(item);
        } else {
            groups.push(vec![item]);
        }
    }
    let mut result: Vec<_> = groups
        .into_iter()
        .map(|group| RowBand {
            members: horizontal(&group, plan).into_iter().map(|item| item.key).collect(),
            bounds: union(group.iter().map(|item| Some(item.bounds.clone()))).unwrap(),
        })
        .collect();
    result.sort_by(|a, b| {
        cmp(a.bounds.center().1, b.bounds.center().1)
            .then_with(|| cmp(a.bounds.y, b.bounds.y))
            .then_with(|| cmp(a.bounds.x, b.bounds.x))
            .then_with(|| compare_keys(plan, &a.members, &b.members))
    });
    result
}

pub(super) fn coordinate(bounds: &Box, kind: &str) -> f64 {
    match kind {
        "left" => bounds.x,
        "right" => bounds.right(),
        _ => bounds.center().0,
    }
}

fn anchor_groups<'a>(
    items: &'a [LayoutItem],
    plan: &GeometricPlan,
    kind: &str,
    scale: f64,
) -> Vec<Vec<&'a LayoutItem>> {
    let mut ordered: Vec<_> = items.iter().collect();
    ordered.sort_by(|a, b| {
        cmp(coordinate(&a.bounds, kind), coordinate(&b.bounds, kind))
            .then_with(|| cmp(a.bounds.y, b.bounds.y))
            .then_with(|| key_name(plan, a.key).cmp(key_name(plan, b.key)))
    });
    let mut groups = Vec::<Vec<&LayoutItem>>::new();
    for item in ordered {
        if let Some(group) = groups.last_mut() {
            let tolerance = 0.25
                * group
                    .iter()
                    .map(|item| item.bounds.height)
                    .fold(scale.min(item.bounds.height), f64::min);
            if coordinate(&item.bounds, kind) - coordinate(&group[0].bounds, kind) <= tolerance {
                group.push(item);
                continue;
            }
        }
        groups.push(vec![item]);
    }
    groups
}

fn unambiguous_members<'a>(
    group: &[&'a LayoutItem],
    row_of: &HashMap<BlockId, usize>,
    plan: &GeometricPlan,
) -> Vec<&'a LayoutItem> {
    let mut counts = HashMap::<usize, usize>::new();
    for item in group {
        *counts.entry(row_of[&item.key]).or_default() += 1;
    }
    let mut candidates: Vec<_> = group
        .iter()
        .copied()
        .filter(|item| counts[&row_of[&item.key]] == 1)
        .collect();
    candidates
        .sort_by(|a, b| cmp(a.bounds.y, b.bounds.y).then_with(|| key_name(plan, a.key).cmp(key_name(plan, b.key))));
    let mut active = Vec::<&LayoutItem>::new();
    let mut conflicting = HashSet::new();
    for item in &candidates {
        active.retain(|previous| previous.bounds.bottom() > item.bounds.y);
        for previous in &active {
            let overlap = previous.bounds.bottom().min(item.bounds.bottom()) - item.bounds.y;
            if overlap > 0.25 * previous.bounds.height.min(item.bounds.height) {
                conflicting.extend([previous.key, item.key]);
            }
        }
        active.push(item);
    }
    candidates.retain(|item| !conflicting.contains(&item.key));
    candidates.sort_by(|a, b| {
        row_of[&a.key]
            .cmp(&row_of[&b.key])
            .then_with(|| cmp(a.bounds.y, b.bounds.y))
            .then_with(|| key_name(plan, a.key).cmp(key_name(plan, b.key)))
    });
    candidates
}

fn corridor(
    items: &[LayoutItem],
    position: f64,
    supported: &HashSet<usize>,
    row_of: &HashMap<BlockId, usize>,
) -> Vec<(f64, f64)> {
    let mut intervals: Vec<_> = items
        .iter()
        .filter(|item| {
            !supported.contains(&row_of[&item.key]) && item.bounds.x < position && position < item.bounds.right()
        })
        .map(|item| (item.bounds.y, item.bounds.bottom()))
        .collect();
    intervals.sort_by(|a, b| cmp(a.0, b.0).then_with(|| cmp(a.1, b.1)));
    let mut highest = f64::NEG_INFINITY;
    for (_, bottom) in &mut intervals {
        highest = highest.max(*bottom);
        *bottom = highest;
    }
    intervals
}

fn blocked(before: &LayoutItem, after: &LayoutItem, intervals: &[(f64, f64)]) -> bool {
    let top = before.bounds.bottom();
    let bottom = after.bounds.y;
    bottom > top
        && intervals
            .partition_point(|&(start, _)| start < bottom)
            .checked_sub(1)
            .is_some_and(|index| intervals[index].1 > top)
}

fn anchors(items: &[LayoutItem], rows: &[RowBand], scale: f64, plan: &GeometricPlan) -> Vec<Anchor> {
    let row_of: HashMap<_, _> = rows
        .iter()
        .enumerate()
        .flat_map(|(index, row)| row.members.iter().map(move |&member| (member, index)))
        .collect();
    let mut output = Vec::new();
    for kind in ["left", "right", "center"] {
        let mut emit = |members: &[&LayoutItem]| {
            if members.len() >= 2 {
                output.push(Anchor {
                    kind,
                    position: median(members.iter().map(|item| coordinate(&item.bounds, kind))),
                    members: members.iter().map(|item| item.key).collect(),
                    rows: members.iter().map(|item| row_of[&item.key]).collect(),
                });
            }
        };
        for group in anchor_groups(items, plan, kind, scale) {
            let members = unambiguous_members(&group, &row_of, plan);
            if members.len() < 2 {
                continue;
            }
            let position = median(members.iter().map(|item| coordinate(&item.bounds, kind)));
            let supported: HashSet<_> = members.iter().map(|item| row_of[&item.key]).collect();
            let intervals = corridor(items, position, &supported, &row_of);
            let mut start = 0;
            for index in 1..members.len() {
                let (before, after) = (members[index - 1], members[index]);
                let overlap = before.bounds.bottom().min(after.bounds.bottom()) - before.bounds.y.max(after.bounds.y);
                if overlap > 0.25 * before.bounds.height.min(after.bounds.height) || blocked(before, after, &intervals)
                {
                    emit(&members[start..index]);
                    start = index;
                }
            }
            emit(&members[start..]);
        }
    }
    output
}

pub(crate) fn analyze(items: &[LayoutItem], plan: &GeometricPlan) -> Option<Alignment> {
    if items.is_empty() {
        return Some(Alignment::default());
    }
    if items.iter().map(|item| item.key).collect::<HashSet<_>>().len() != items.len()
        || items.iter().any(|item| item.bounds.space != items[0].bounds.space)
    {
        return None;
    }
    let scale = median(items.iter().map(|item| item.bounds.height));
    let rows = rows(items, plan);
    let by_key: HashMap<_, _> = items.iter().map(|item| (item.key, item)).collect();
    let ordered = rows
        .iter()
        .flat_map(|row| row.members.iter().map(|id| by_key[id].clone()))
        .collect();
    Some(Alignment {
        anchors: anchors(items, &rows, scale, plan),
        scale,
        rows,
        items: ordered,
    })
}
