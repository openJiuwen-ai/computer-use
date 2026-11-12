// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::collections::{HashMap, HashSet};

use super::{
    alignment::{analyze, cmp, compare_keys, median, Alignment, LayoutItem},
    scopes::stable_order,
    BlockId, GeometricPlan, GeometryError, Result,
};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(super) enum Axis {
    Horizontal,
    Vertical,
}

#[derive(Clone, Debug)]
pub(super) struct Region {
    pub(super) keys: Vec<BlockId>,
    pub(super) axis: Option<Axis>,
    pub(super) children: Vec<Region>,
}

impl Region {
    pub(super) fn leaf(keys: Vec<BlockId>) -> Self {
        Self {
            keys,
            axis: None,
            children: Vec::new(),
        }
    }
}

#[derive(Clone, Copy)]
struct Gap {
    axis: Axis,
    start: f64,
    stop: f64,
}

impl Gap {
    fn width(self) -> f64 {
        self.stop - self.start
    }
}

struct Cut {
    gap: Gap,
    first: Vec<BlockId>,
    second: Vec<BlockId>,
    score: f64,
}

fn gaps(items: &[&LayoutItem], axis: Axis) -> Vec<Gap> {
    let mut intervals: Vec<_> = items
        .iter()
        .map(|item| match axis {
            Axis::Vertical => (item.bounds.x, item.bounds.right()),
            Axis::Horizontal => (item.bounds.y, item.bounds.bottom()),
        })
        .collect();
    intervals.sort_by(|a, b| cmp(a.0, b.0).then_with(|| cmp(a.1, b.1)));
    let Some(first) = intervals.first() else {
        return Vec::new();
    };
    let mut end = first.1;
    let mut result = Vec::new();
    for &(start, stop) in &intervals[1..] {
        if start > end {
            result.push(Gap {
                axis,
                start: end,
                stop: start,
            });
        }
        end = end.max(stop);
    }
    result
}

fn row_sets(keys: &HashSet<BlockId>, alignment: &Alignment) -> Vec<HashSet<BlockId>> {
    alignment
        .rows
        .iter()
        .filter_map(|row| {
            let members: HashSet<_> = row.members.iter().filter(|key| keys.contains(key)).copied().collect();
            (!members.is_empty()).then_some(members)
        })
        .collect()
}

fn independent_groups(
    first: &HashSet<BlockId>,
    second: &HashSet<BlockId>,
    groups: &[HashSet<BlockId>],
    alignment: &Alignment,
) -> bool {
    [first, second].into_iter().all(|side| {
        let rows = row_sets(side, alignment);
        groups.iter().any(|group| {
            group.len() >= 2
                && group.is_subset(side)
                && rows.iter().filter(|row| !group.is_disjoint(row)).count() as f64
                    >= 2.0_f64.max(0.6 * rows.len() as f64)
        })
    })
}

type Items<'a> = HashMap<BlockId, &'a LayoutItem>;
type Edge = (BlockId, BlockId);

fn pane(keys: &[BlockId], independent: &HashSet<BlockId>) -> bool {
    keys.len() == 1 && independent.contains(&keys[0])
}

fn columns_supported(
    first: &[BlockId],
    second: &[BlockId],
    items: &Items<'_>,
    groups: &[HashSet<BlockId>],
    alignment: &Alignment,
    independent: &HashSet<BlockId>,
) -> bool {
    let left: HashSet<_> = first.iter().copied().collect();
    let right: HashSet<_> = second.iter().copied().collect();
    let rows_left = row_sets(&left, alignment);
    let rows_right = row_sets(&right, alignment);
    if rows_left.len() < 2 && !pane(first, independent) || rows_right.len() < 2 && !pane(second, independent) {
        return false;
    }
    let band = |keys: &[BlockId]| {
        let top = keys.iter().map(|key| items[key].bounds.y).reduce(f64::min).unwrap();
        let bottom = keys
            .iter()
            .map(|key| items[key].bounds.bottom())
            .reduce(f64::max)
            .unwrap();
        (top, bottom)
    };
    let ((a, b), (c, d)) = (band(first), band(second));
    if b.min(d) - a.max(c) < 0.6 * (b - a).min(d - c) {
        return false;
    }
    let paired = alignment
        .rows
        .iter()
        .filter(|row| {
            row.members.iter().any(|key| left.contains(key)) && row.members.iter().any(|key| right.contains(key))
        })
        .count();
    if paired >= 2 && paired as f64 >= 0.6 * rows_left.len().min(rows_right.len()) as f64 {
        return independent_groups(&left, &right, groups, alignment);
    }
    true
}

fn spanning_boundary(
    first: &[BlockId],
    second: &[BlockId],
    items: &Items<'_>,
    scale: f64,
    independent: &HashSet<BlockId>,
) -> bool {
    [(first, second), (second, first)].into_iter().any(|(spanning, body)| {
        if body.len() < 4 && !(body.len() >= 3 && body.iter().any(|key| independent.contains(key))) {
            return false;
        }
        let local: Vec<_> = body.iter().map(|key| items[key]).collect();
        gaps(&local, Axis::Vertical).into_iter().any(|gutter| {
            let left: Vec<_> = body
                .iter()
                .filter(|key| items[key].bounds.right() <= gutter.start)
                .copied()
                .collect();
            let right: Vec<_> = body
                .iter()
                .filter(|key| items[key].bounds.x >= gutter.stop)
                .copied()
                .collect();
            let pane_left = pane(&left, independent);
            let pane_right = pane(&right, independent);
            gutter.width()
                >= if pane_left || pane_right {
                    0.25 * scale
                } else {
                    0.75 * scale
                }
                && (left.len() >= 2 || pane_left)
                && (right.len() >= 2 || pane_right)
                && spanning
                    .iter()
                    .any(|key| items[key].bounds.x < gutter.start && items[key].bounds.right() > gutter.stop)
        })
    })
}

fn repeated_pair(first: &[BlockId], second: &[BlockId], alignment: &Alignment) -> bool {
    let upper: HashSet<_> = first.iter().copied().collect();
    let lower: HashSet<_> = second.iter().copied().collect();
    if row_sets(&upper, alignment).len() != 1 || row_sets(&lower, alignment).len() != 1 {
        return false;
    }
    let pairs: Vec<_> = alignment
        .anchors
        .iter()
        .filter_map(|anchor| {
            let upper: Vec<_> = anchor
                .members
                .iter()
                .filter(|key| upper.contains(key))
                .copied()
                .collect();
            let lower: Vec<_> = anchor
                .members
                .iter()
                .filter(|key| lower.contains(key))
                .copied()
                .collect();
            match (upper.as_slice(), lower.as_slice()) {
                ([a], [b]) => Some((*a, *b)),
                _ => None,
            }
        })
        .collect();
    pairs.iter().any(|&(a, b)| pairs.iter().any(|&(c, d)| a != c && b != d))
}

fn cut_order(first: &[BlockId], second: &[BlockId], edges: &[Edge]) -> Option<bool> {
    let forward = edges.iter().any(|(a, b)| first.contains(a) && second.contains(b));
    let backward = edges.iter().any(|(a, b)| second.contains(a) && first.contains(b));
    (!(forward && backward)).then_some(backward)
}

struct Partition<'a> {
    plan: &'a GeometricPlan,
    items: Items<'a>,
    groups: Vec<HashSet<BlockId>>,
    before: &'a [Edge],
    independent: HashSet<BlockId>,
}

impl Partition<'_> {
    fn choose(&self, keys: &[BlockId], alignment: &Alignment) -> Result<Region> {
        let geometric: Vec<_> = alignment
            .rows
            .iter()
            .flat_map(|row| row.members.iter().copied())
            .collect();
        if geometric.len() != keys.len()
            || geometric.iter().copied().collect::<HashSet<_>>() != keys.iter().copied().collect()
        {
            return Err(GeometryError::Invalid(
                "geometric partition alignment differs from its input",
            ));
        }
        let keys =
            stable_order(&geometric, self.before).ok_or(GeometryError::Invalid("cyclic partition precedence"))?;
        if keys.len() < 2 {
            return Ok(Region::leaf(keys));
        }
        let local: Vec<_> = keys.iter().map(|key| self.items[key]).collect();
        let scale = median(local.iter().map(|item| item.bounds.height));
        let vertical = gaps(&local, Axis::Vertical);
        let horizontal = gaps(&local, Axis::Horizontal);
        let ordinary_gap = if horizontal.is_empty() {
            0.0
        } else {
            median(horizontal.iter().map(|gap| gap.width()))
        };
        let width = local.iter().map(|item| item.bounds.right()).reduce(f64::max).unwrap()
            - local.iter().map(|item| item.bounds.x).reduce(f64::min).unwrap();
        let key_set: HashSet<_> = keys.iter().copied().collect();
        let cuts = vertical.iter().chain(&horizontal).filter_map(|&gap| {
            let (first, second): (Vec<_>, Vec<_>) = keys.iter().copied().partition(|key| {
                let bounds = &self.items[key].bounds;
                (match gap.axis {
                    Axis::Vertical => bounds.right(),
                    Axis::Horizontal => bounds.bottom(),
                }) <= gap.start
            });
            if first.is_empty() || second.is_empty() {
                return None;
            }
            if self.groups.iter().any(|group| {
                group.is_subset(&key_set)
                    && group.iter().any(|key| first.contains(key))
                    && group.iter().any(|key| second.contains(key))
            }) {
                return None;
            }
            if gap.axis == Axis::Vertical {
                let minimum = if pane(&first, &self.independent) || pane(&second, &self.independent) {
                    0.25 * scale
                } else {
                    (1.25 * scale).min(0.06 * width)
                };
                if gap.width() < minimum {
                    return None;
                }
            }
            let reverse = cut_order(&first, &second, self.before)?;
            let spanning = if gap.axis == Axis::Vertical {
                if !columns_supported(&first, &second, &self.items, &self.groups, alignment, &self.independent) {
                    return None;
                }
                false
            } else {
                let spanning = spanning_boundary(&first, &second, &self.items, scale, &self.independent);
                if horizontal.len() == 1 && !spanning && repeated_pair(&first, &second, alignment) {
                    return None;
                }
                let mut threshold = 1.5 * scale;
                if horizontal.len() > 1 {
                    threshold = threshold.max(1.65 * ordinary_gap);
                }
                if gap.width() < threshold && !spanning {
                    return None;
                }
                spanning
            };
            let balance = first.len().min(second.len()) as f64 / keys.len() as f64;
            let score = gap.width() / scale + 0.1 * balance + if spanning { 1.0 } else { 0.0 };
            let (first, second) = if reverse { (second, first) } else { (first, second) };
            Some(Cut {
                gap,
                first,
                second,
                score,
            })
        });
        let best = cuts.min_by(|a, b| {
            cmp(b.score, a.score)
                .then_with(|| a.gap.axis.cmp(&b.gap.axis))
                .then_with(|| cmp(a.gap.start, b.gap.start))
                .then_with(|| compare_keys(self.plan, &a.first, &b.first))
                .then_with(|| compare_keys(self.plan, &a.second, &b.second))
        });
        let Some(cut) = best else {
            return Ok(Region::leaf(keys));
        };
        let keys = cut.first.iter().chain(&cut.second).copied().collect();
        Ok(Region {
            keys,
            axis: Some(cut.gap.axis),
            children: vec![Region::leaf(cut.first), Region::leaf(cut.second)],
        })
    }

    fn select(&self, keys: &[BlockId], alignment: Option<&Alignment>, depth: usize) -> Result<Region> {
        let owned;
        let alignment = if let Some(alignment) = alignment {
            alignment
        } else {
            let local = keys.iter().map(|key| self.items[key].clone()).collect::<Vec<_>>();
            owned = analyze(&local, self.plan)
                .ok_or(GeometryError::Invalid("geometric partition requires comparable boxes"))?;
            &owned
        };
        let choice = self.choose(keys, alignment)?;
        if choice.children.is_empty() || depth >= 32 {
            return Ok(Region::leaf(choice.keys));
        }
        let children = choice
            .children
            .iter()
            .map(|child| self.select(&child.keys, None, depth + 1))
            .collect::<Result<Vec<_>>>()?;
        let keys = children.iter().flat_map(|child| child.keys.iter().copied()).collect();
        Ok(Region {
            keys,
            axis: choice.axis,
            children,
        })
    }
}

pub(super) fn partition(
    plan: &GeometricPlan,
    items: &[LayoutItem],
    together: &[Vec<BlockId>],
    before: &[Edge],
    alignment: Option<&Alignment>,
    independent: &[BlockId],
) -> Result<Region> {
    let measured: Items<'_> = items.iter().map(|item| (item.key, item)).collect();
    if measured.len() != items.len() {
        return Err(GeometryError::Invalid(
            "geometric partition contains duplicate unit keys",
        ));
    }
    if items
        .first()
        .is_some_and(|first| items.iter().any(|item| item.bounds.space != first.bounds.space))
    {
        return Err(GeometryError::Invalid(
            "geometric partition requires comparable coordinate spaces",
        ));
    }
    let groups: Vec<_> = together
        .iter()
        .filter(|group| !group.is_empty())
        .map(|group| group.iter().copied().collect())
        .collect();
    if together.iter().flatten().any(|key| !measured.contains_key(key))
        || before
            .iter()
            .any(|(a, b)| !measured.contains_key(a) || !measured.contains_key(b))
    {
        return Err(GeometryError::Invalid(
            "geometric partition constraint is outside its input",
        ));
    }
    let partition = Partition {
        plan,
        items: measured,
        groups,
        before,
        independent: independent.iter().copied().collect(),
    };
    let keys = items.iter().map(|item| item.key).collect::<Vec<_>>();
    partition.select(&keys, alignment, 0)
}

pub(super) fn paths(forest: &[Region]) -> Result<Vec<(BlockId, Vec<usize>)>> {
    let mut pending: Vec<_> = forest
        .iter()
        .enumerate()
        .rev()
        .map(|(i, region)| (region, vec![i]))
        .collect();
    let mut paths = Vec::new();
    let mut seen = HashSet::new();
    while let Some((region, path)) = pending.pop() {
        if region.children.is_empty() {
            for &key in &region.keys {
                if !seen.insert(key) {
                    return Err(GeometryError::Invalid(
                        "geometric region unit occurs in multiple leaves",
                    ));
                }
                paths.push((key, path.clone()));
            }
        } else {
            if !region
                .keys
                .iter()
                .eq(region.children.iter().flat_map(|child| &child.keys))
            {
                return Err(GeometryError::Invalid(
                    "geometric region children changed parent membership",
                ));
            }
            pending.extend(region.children.iter().enumerate().rev().map(|(i, child)| {
                let mut path = path.clone();
                path.push(i);
                (child, path)
            }));
        }
    }
    Ok(paths)
}
