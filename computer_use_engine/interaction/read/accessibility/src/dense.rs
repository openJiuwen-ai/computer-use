// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Fused lexical proof and exact reconstruction of dense observation frames.

use std::collections::{HashMap, TryReserveError};

use super::json_cell::{one_value_at_depth, MAX_DEPTH as LEGACY_JSON_DEPTH};

const ABSENT: u32 = u32::MAX;
const POOLED_VALUE: u32 = 1 << 31;
const MAX_POOLED_ENTRIES: usize = 4_096;
const MAX_POOLED_BYTES: usize = 1 << 20;
const MAX_POOLED_ENTRY_BYTES: usize = 16 << 10;
const MAX_POOLED_REFERENCES: usize = 65_536;
const MAX_NODES: usize = 65_536;
const MAX_SPARSE_SLOTS: usize = 65_536;
const MAX_BYTES: usize = 64 * 1024 * 1024 - 55;
const ROW_HEADER: usize = 5 * 4;
const CUS2_HEADER: usize = 4 + 4 * 4;
const OWNER_PREFIX: &[u8] = b"[[\"batch_owner\",{\"metadataValue\":";
const INDEX_PREFIX: &[u8] = b"}],[\"batch_index\",{\"metadataValue\":";
const BATCH_SUFFIX: &[u8] = b"}]]";
const DETAILS: &[u8] = b"{\"details\":";
const COMPLETE: &[u8] = b"{\"complete\":";
const COVERED_DETAILS: &[u8] = b",\"details\":";
const ISSUES: &[u8] = b",\"issues\":";

#[derive(Debug, PartialEq, Eq)]
pub(super) enum Error {
    Invalid(&'static str),
    Allocation,
}

type Checked<T> = Result<T, Error>;
const BOUNDS: Error = Error::Invalid("dense observation: invalid packed bounds");
const BUDGET: Error = Error::Invalid("dense observation: output byte budget exceeded");
const RESTORED: Error = Error::Invalid("dense observation: inconsistent original size");
const FRAME_SIZE: Error = Error::Invalid("segmented observation: invalid frame size");
const UNUSED: Error = Error::Invalid("segmented observation: unused pool fragment");

impl From<TryReserveError> for Error {
    fn from(_: TryReserveError) -> Self {
        Self::Allocation
    }
}

fn take<'a>(source: &mut &'a [u8], length: usize) -> Checked<&'a [u8]> {
    let (bytes, tail) = source.split_at_checked(length).ok_or(BOUNDS)?;
    *source = tail;
    Ok(bytes)
}

fn u32(source: &mut &[u8]) -> Checked<u32> {
    let bytes = take(source, 4)?.try_into().map_err(|_| BOUNDS)?;
    Ok(u32::from_le_bytes(bytes))
}

fn append(out: &mut Vec<u8>, bytes: &[u8], limit: usize) -> Checked<()> {
    if bytes.len() > limit.saturating_sub(out.len()) {
        return Err(BUDGET);
    }
    let required = out.len() + bytes.len();
    if required > out.capacity() {
        let capacity = out.capacity().saturating_mul(2).max(4096).max(required).min(limit);
        out.try_reserve_exact(capacity - out.len())?;
    }
    out.extend_from_slice(bytes);
    Ok(())
}

fn append_all(out: &mut Vec<u8>, parts: &[&[u8]], limit: usize) -> Checked<()> {
    for bytes in parts {
        append(out, bytes, limit)?;
    }
    Ok(())
}

struct Decimal {
    bytes: [u8; 10],
    start: usize,
}

impl Decimal {
    fn new(mut value: u32) -> Self {
        let mut bytes = [0; 10];
        let mut start = bytes.len();
        loop {
            start -= 1;
            bytes[start] = b'0' + (value % 10) as u8;
            value /= 10;
            if value == 0 {
                return Self { bytes, start };
            }
        }
    }

    fn bytes(&self) -> &[u8] {
        &self.bytes[self.start..]
    }
}

enum ReadLayout<'a> {
    Json(&'a [u8]),
    Batch {
        owner: Decimal,
        index: Decimal,
        complete: Option<&'static [u8]>,
        issues: &'a [u8],
    },
}

struct ReadFragment<'a> {
    raw: &'a [u8],
    layout: ReadLayout<'a>,
    restored_size: usize,
}

impl<'a> ReadFragment<'a> {
    fn new(raw: &'a [u8]) -> Checked<Self> {
        let (&tag, mut tail) = raw.split_first().ok_or(BOUNDS)?;
        if tag == b'j' {
            if tail.is_empty() {
                return Err(Error::Invalid("segmented observation: empty read record"));
            }
            return Ok(Self {
                raw,
                layout: ReadLayout::Json(tail),
                restored_size: tail.len(),
            });
        }
        let complete: Option<&'static [u8]> = match tag {
            b'b' if tail.len() == 8 => None,
            b't' if tail.len() > 8 => Some(b"true"),
            b'f' if tail.len() > 8 => Some(b"false"),
            b'n' if tail.len() > 8 => Some(b"null"),
            b'b' => return Err(Error::Invalid("segmented observation: invalid batch record")),
            _ => return Err(Error::Invalid("segmented observation: invalid covered batch record")),
        };
        let owner = Decimal::new(u32(&mut tail)?);
        let index = Decimal::new(u32(&mut tail)?);
        let details = OWNER_PREFIX.len()
            + owner.bytes().len()
            + INDEX_PREFIX.len()
            + index.bytes().len()
            + BATCH_SUFFIX.len();
        let restored_size = details + 1 + match complete {
            Some(value) => COMPLETE.len() + value.len() + COVERED_DETAILS.len() + ISSUES.len() + tail.len(),
            None => DETAILS.len(),
        };
        Ok(Self {
            raw,
            layout: ReadLayout::Batch { owner, index, complete, issues: tail },
            restored_size,
        })
    }

    fn write_original(&self, out: &mut Vec<u8>, limit: usize) -> Checked<()> {
        match &self.layout {
            ReadLayout::Json(value) => append(out, value, limit),
            ReadLayout::Batch { owner, index, complete, issues } => {
                if let Some(value) = complete {
                    append_all(out, &[COMPLETE, value, COVERED_DETAILS], limit)?;
                } else {
                    append(out, DETAILS, limit)?;
                }
                append_all(out, &[OWNER_PREFIX, owner.bytes(), INDEX_PREFIX, index.bytes(), BATCH_SUFFIX], limit)?;
                if complete.is_some() {
                    append_all(out, &[ISSUES, issues], limit)?;
                }
                append(out, b"}", limit)
            }
        }
    }

    fn proves_value(&self, depth: usize) -> bool {
        match &self.layout {
            ReadLayout::Json(value) => one_value_at_depth(value, depth),
            ReadLayout::Batch { complete, issues, .. } => complete.is_none() || one_value_at_depth(issues, depth),
        }
    }
}

struct Packet<'a> {
    key_pool: &'a [u8],
    read_pool: &'a [u8],
    keys: Vec<&'a [u8]>,
    reads: Vec<ReadFragment<'a>>,
    values: Vec<&'a [u8]>,
    pooled: bool,
    tape: &'a [u8],
    node_count: usize,
    original_size: usize,
    limit: usize,
}

fn pool<'a, T>(mut source: &'a [u8], max_entries: usize, decode: impl Fn(&'a [u8]) -> Checked<T>) -> Checked<Vec<T>> {
    let mut entries = Vec::new();
    while !source.is_empty() {
        let length = u32(&mut source)? as usize;
        if length == 0 || entries.len() == max_entries {
            return Err(BOUNDS);
        }
        let entry = decode(take(&mut source, length)?)?;
        entries.try_reserve(1)?;
        entries.push(entry);
    }
    Ok(entries)
}

impl<'a> Packet<'a> {
    fn new(mut source: &'a [u8], limit: usize, pooled: bool) -> Checked<Self> {
        let limit = limit.min(MAX_BYTES);
        let magic = take(&mut source, 4)?;
        let expected_magic = if pooled { b"CUD2" } else { b"CUD1" };
        if source.len() + 4 > limit || magic != expected_magic {
            return Err(BOUNDS);
        }
        let key_size = u32(&mut source)? as usize;
        let read_size = u32(&mut source)? as usize;
        let value_size = if pooled { u32(&mut source)? as usize } else { 0 };
        let tape_size = u32(&mut source)? as usize;
        let node_count = u32(&mut source)? as usize;
        let original_size = u32(&mut source)? as usize;
        if node_count > MAX_NODES || original_size == 0 || original_size > limit || value_size > MAX_POOLED_BYTES {
            return Err(BOUNDS);
        }
        let key_pool = take(&mut source, key_size)?;
        let read_pool = take(&mut source, read_size)?;
        let value_pool = take(&mut source, value_size)?;
        if source.len() != tape_size {
            return Err(BOUNDS);
        }
        // Every pool entry needs at least one property slot. CUD1 rows
        // contain at least one inline value byte. A CUD2 row may refer to its pool.
        let max_rows = tape_size / (ROW_HEADER + usize::from(!pooled));
        let keys = pool(key_pool, max_rows, Ok)?;
        let reads = pool(read_pool, max_rows, ReadFragment::new)?;
        let values = pool(value_pool, max_rows.min(MAX_POOLED_ENTRIES), |entry| {
            if entry.len() > MAX_POOLED_ENTRY_BYTES { Err(BOUNDS) } else { Ok(entry) }
        })?;
        Ok(Self { key_pool, read_pool, keys, reads, values, pooled, tape: source, node_count, original_size, limit })
    }
}

struct Row<'a, 'packet> {
    key_index: u32,
    read_index: u32,
    key: &'a [u8],
    read: Option<&'packet ReadFragment<'a>>,
    arguments: &'a [u8],
    state: &'a [u8],
    value: &'a [u8],
    value_index: Option<u32>,
}

impl<'a, 'packet> Row<'a, 'packet> {
    fn new(source: &mut &'a [u8], packet: &'packet Packet<'a>) -> Checked<Self> {
        let key_index = u32(source)?;
        let read_index = u32(source)?;
        let key = *packet.keys.get(key_index as usize).ok_or(BOUNDS)?;
        let read = if read_index == ABSENT { None } else { Some(packet.reads.get(read_index as usize).ok_or(BOUNDS)?) };
        let arguments_len = u32(source)? as usize;
        let state_len = u32(source)? as usize;
        let value_len = u32(source)? as usize;
        if value_len == 0 {
            return Err(BOUNDS);
        }
        let arguments = take(source, arguments_len)?;
        let state = take(source, state_len)?;
        let (value, value_index) = if packet.pooled && value_len & POOLED_VALUE as usize != 0 {
            let index = value_len as u32 & !POOLED_VALUE;
            (*packet.values.get(index as usize).ok_or(BOUNDS)?, Some(index))
        } else {
            (take(source, value_len)?, None)
        };
        Ok(Self { key_index, read_index, key, read, arguments, state, value, value_index })
    }

    fn restored_size(&self) -> usize {
        let arguments = if self.arguments.is_empty() { 0 } else { b"\"arguments\":,".len() + self.arguments.len() };
        let state = if self.state.is_empty() { 0 } else { b",\"state\":".len() + self.state.len() };
        let read = self.read.map_or(0, |fragment| b",\"read\":".len() + fragment.restored_size);
        b"{\"key\":,\"value\":}".len() + self.key.len() + self.value.len() + arguments + state + read
    }

    fn proves_cells(&self, depth: usize) -> bool {
        (self.arguments.is_empty() || one_value_at_depth(self.arguments, depth))
            && (self.state.is_empty() || one_value_at_depth(self.state, depth))
            && (self.value_index.is_some() || one_value_at_depth(self.value, depth))
    }

    fn ordinary(&self) -> Option<Slot> {
        let index = self.value_index?;
        if !self.arguments.is_empty() {
            return None;
        }
        let value = if self.value.starts_with(b"{\"data\":") {
            index
        } else if self.value == b"null" {
            ABSENT
        } else {
            return None;
        };
        Some(Slot {
            tag: self.key_index.checked_mul(4)? | u32::from(!self.state.is_empty()),
            read: self.read_index,
            value,
        })
    }

    // Charge the original dense text size even when the ordinary scalar cells
    // are omitted. Its legacy byte-bound fallback is part of admission.
    fn ordinary_dense_size(&self, slot: Slot) -> usize {
        fn digits(number: u32) -> usize {
            number.checked_ilog10().map_or(1, |power| power as usize + 1)
        }
        let tag = slot.tag | if slot.value == ABSENT { 0 } else { POOLED_VALUE };
        let read = if slot.read == ABSENT { 2 } else { digits(slot.read) };
        let value = if slot.value == ABSENT { 4 } else { digits(slot.value) };
        let state = if self.state.is_empty() { 4 } else { self.state.len() };
        5 + 4 + digits(tag) + read + state + value
    }

    fn write_dense(&self, out: &mut Vec<u8>, limit: usize) -> Checked<()> {
        let pooled = self.value_index.filter(|_| self.value.starts_with(b"{\"data\":"));
        let tag = self.key_index.checked_mul(4).ok_or(BOUNDS)?
            | if pooled.is_some() { POOLED_VALUE } else { 0 }
            | if self.arguments.is_empty() { 0 } else { 2 }
            | if self.state.is_empty() { 0 } else { 1 };
        let tag = Decimal::new(tag);
        let read = Decimal::new(self.read_index);
        let arguments = if self.arguments.is_empty() { b"null" } else { self.arguments };
        let state = if self.state.is_empty() { b"null" } else { self.state };
        let read_id = if self.read.is_some() { read.bytes() } else { b"-1" };
        append_all(out, &[b",", arguments, b",", tag.bytes(), b",", read_id, b",", state, b","], limit)?;
        if let Some(index) = pooled {
            return append(out, Decimal::new(index).bytes(), limit);
        }
        append(out, self.value, limit)
    }

    fn write_property(&self, out: &mut Vec<u8>, limit: usize, mut gaps: Option<&mut Gaps>) -> Checked<()> {
        if self.arguments.is_empty() {
            append(out, b"{\"key\":", limit)?;
        } else {
            append_all(out, &[b"{\"arguments\":", self.arguments, b",\"key\":"], limit)?;
        }
        if let Some(gaps) = &mut gaps {
            gaps.push(out.len(), self.key_index, 0, limit)?;
        } else {
            append(out, self.key, limit)?;
        }
        if let Some(read) = self.read {
            append(out, b",\"read\":", limit)?;
            if let Some(gaps) = gaps {
                gaps.push(out.len(), self.read_index, 1, limit)?;
            } else {
                read.write_original(out, limit)?;
            }
        }
        if !self.state.is_empty() {
            append_all(out, &[b",\"state\":", self.state], limit)?;
        }
        append_all(out, &[b",\"value\":", self.value, b"}"], limit)
    }
}

#[derive(Default)]
struct Gaps {
    bytes: Vec<u8>,
    previous: usize,
}

impl Gaps {
    fn push(&mut self, at: usize, index: u32, kind: u32, limit: usize) -> Checked<()> {
        let distance = u32::try_from(at - self.previous).map_err(|_| BOUNDS)?;
        let tag = index.checked_mul(2).ok_or(BOUNDS)? | kind;
        append_all(&mut self.bytes, &[&distance.to_le_bytes(), &tag.to_le_bytes()], limit)?;
        self.previous = at;
        Ok(())
    }
}

#[derive(Clone, Copy)]
pub(super) struct Slot {
    pub(super) tag: u32,
    pub(super) read: u32,
    pub(super) value: u32,
}

pub(super) const RICH_SLOT: u32 = u32::MAX;
pub(super) const NO_COVERED_GROUP: u16 = u16::MAX;
const MAX_COVERED_GROUPS: usize = 64;
const MAX_COVERED_BYTES: usize = 4096;

#[derive(Default)]
pub(super) struct SparsePlan {
    pub(super) slots: Vec<Slot>,
    pub(super) ends: Vec<usize>,
    pub(super) bound: Vec<bool>,
    pub(super) batch_bounds: Vec<u32>,
    pub(super) covered_groups: Vec<u16>,
    pub(super) covered_sources: Vec<Vec<u8>>,
    pub(super) rich_binds: Vec<(usize, usize, u32)>,
    omitted: usize,
}

enum Assembly {
    Dense { text: Vec<u8>, original: Vec<u8>, widths: Vec<u32>, sparse: Option<SparsePlan> },
    Legacy { literal: Vec<u8>, gaps: Gaps },
    Original { text: Vec<u8>, property_offsets: Vec<usize> },
}

impl Assembly {
    fn begin_node(&mut self, prefix: &[u8], rows: u32, packet: &Packet<'_>) -> Checked<()> {
        match self {
            Self::Dense { text, original, widths, sparse } => {
                if let Some(plan) = sparse {
                    if rows as usize > MAX_SPARSE_SLOTS - plan.slots.len() {
                        return Err(BUDGET);
                    }
                    plan.slots.try_reserve(rows as usize)?;
                    widths.push(0);
                } else {
                    widths.push(rows);
                }
                let omitted = sparse.as_ref().map_or(0, |plan| plan.omitted);
                append_all(text, &[prefix, b"[[\nNaN\n"], packet.limit - omitted)?;
                append_all(original, &[prefix, b"["], packet.original_size)
            }
            Self::Legacy { literal, .. } => append_all(literal, &[prefix, b"["], packet.limit),
            Self::Original { text, property_offsets } => {
                append(text, prefix, packet.original_size)?;
                property_offsets.push(text.len());
                append(text, b"[", packet.original_size)
            }
        }
    }

    fn property(&mut self, row: &Row<'_, '_>, ordinal: u32, packet: &Packet<'_>) -> Checked<()> {
        match self {
            Self::Dense { text, original, widths, sparse } => {
                if let Some(plan) = sparse {
                    let bound_index = row.value_index.filter(|_| row.value.starts_with(b"{\"data\":"));
                    if let Some(index) = bound_index {
                        plan.bound[index as usize] = true;
                    }
                    if let Some(slot) = row.ordinary() {
                        plan.slots.push(slot);
                        let retained = usize::from(!row.state.is_empty());
                        let emitted = retained * (1 + row.state.len());
                        plan.omitted += row.ordinary_dense_size(slot) - emitted;
                        let limit = packet.limit.checked_sub(plan.omitted).ok_or(BUDGET)?;
                        if text.len() > limit {
                            return Err(BUDGET);
                        }
                        if retained != 0 {
                            append_all(text, &[b",", row.state], limit)?;
                            *widths.last_mut().unwrap() += 1;
                        }
                    } else {
                        plan.slots.push(Slot { tag: RICH_SLOT, read: 0, value: 0 });
                        if let Some(index) = bound_index {
                            plan.rich_binds.try_reserve(1)?;
                            plan.rich_binds.push((plan.ends.len(), *widths.last().unwrap() as usize, index));
                        }
                        row.write_dense(text, packet.limit - plan.omitted)?;
                        *widths.last_mut().unwrap() += 5;
                    }
                } else {
                    row.write_dense(text, packet.limit)?;
                }
                if ordinal > 0 {
                    append(original, b",", packet.original_size)?;
                }
                row.write_property(original, packet.original_size, None)
            }
            Self::Legacy { literal, gaps } => {
                if ordinal > 0 {
                    append(literal, b",", packet.limit)?;
                }
                row.write_property(literal, packet.limit, Some(gaps))
            }
            Self::Original { text, .. } => {
                if ordinal > 0 {
                    append(text, b",", packet.original_size)?;
                }
                row.write_property(text, packet.original_size, None)
            }
        }
    }

    fn end_node(&mut self, packet: &Packet<'_>) -> Checked<()> {
        match self {
            Self::Dense { text, original, sparse, .. } => {
                let omitted = sparse.as_ref().map_or(0, |plan| plan.omitted);
                append(text, b"]]", packet.limit - omitted)?;
                if let Some(plan) = sparse {
                    plan.ends.push(plan.slots.len());
                }
                append(original, b"]", packet.original_size)
            }
            Self::Legacy { literal, .. } => append(literal, b"]", packet.limit),
            Self::Original { text, .. } => append(text, b"]", packet.original_size),
        }
    }
}

fn emit_if_available(
    output: &mut Option<Assembly>,
    dense: bool,
    emit: impl FnOnce(&mut Assembly) -> Checked<()>,
) -> Checked<()> {
    let result = output.as_mut().map_or(Ok(()), emit);
    // A dense-only marker overhead can exceed the bound for an otherwise
    // admissible original frame. Keep checking structure before falling back.
    if dense && result == Err(BUDGET) {
        *output = None;
        return Ok(());
    }
    result
}

struct Scan<'a> {
    output: Option<Assembly>,
    counts: (usize, usize),
    tail: &'a [u8],
}

#[derive(Clone, Copy)]
enum ScanMode {
    Dense { sparse: bool },
    Legacy,
    Original { depth: usize },
}

fn scan<'a>(packet: &Packet<'a>, mode: ScanMode) -> Checked<Scan<'a>> {
    let mut source = packet.tape;
    let mut restored = 0_usize;
    let mut pooled_references = 0;
    let mut counts = (0, 0);
    let key_count = packet.keys.len();
    let read_end = key_count + packet.reads.len();
    let slot_count = read_end + packet.values.len();
    let mut used = Vec::new();
    used.try_reserve_exact(slot_count)?;
    used.resize(slot_count, false);
    let dense = matches!(mode, ScanMode::Dense { .. });
    let depth = match mode {
        ScanMode::Original { depth } => depth,
        _ => LEGACY_JSON_DEPTH,
    };
    let mut output = match mode {
        ScanMode::Legacy => Some(Assembly::Legacy { literal: Vec::new(), gaps: Gaps::default() }),
        _ if !packet.values.iter().all(|value| one_value_at_depth(value, depth)) => None,
        ScanMode::Original { .. } => {
            if packet.keys.iter().all(|key| one_value_at_depth(key, depth))
                && packet.reads.iter().all(|read| read.proves_value(depth))
            {
                let mut text = Vec::new();
                let mut property_offsets = Vec::new();
                text.try_reserve_exact(packet.original_size)?;
                property_offsets.try_reserve_exact(packet.node_count)?;
                Some(Assembly::Original { text, property_offsets })
            } else {
                None
            }
        }
        ScanMode::Dense { sparse } => {
            let mut original = Vec::new();
            let mut widths = Vec::new();
            original.try_reserve_exact(packet.original_size)?;
            widths.try_reserve_exact(packet.node_count)?;
            let sparse = if sparse {
                let mut plan = SparsePlan::default();
                plan.ends.try_reserve_exact(packet.node_count)?;
                plan.bound.try_reserve_exact(packet.values.len())?;
                plan.bound.resize(packet.values.len(), false);
                Some(plan)
            } else {
                None
            };
            Some(Assembly::Dense { text: Vec::new(), original, widths, sparse })
        }
    };
    for _ in 0..packet.node_count {
        let prefix_len = u32(&mut source)? as usize;
        let rows = u32(&mut source)?;
        let prefix = take(&mut source, prefix_len)?;
        if rows as usize > source.len() / (ROW_HEADER + usize::from(!packet.pooled)) {
            return Err(BOUNDS);
        }
        restored += prefix_len + 2 + (rows as usize).saturating_sub(1);
        if restored > packet.original_size {
            return Err(RESTORED);
        }
        emit_if_available(&mut output, dense, |out| out.begin_node(prefix, rows, packet))?;
        for ordinal in 0..rows {
            let row = Row::new(&mut source, packet)?;
            restored += row.restored_size();
            if restored > packet.original_size {
                return Err(RESTORED);
            }
            counts.0 += 1;
            used[row.key_index as usize] = true;
            if row.read.is_some() {
                counts.1 += 1;
                used[key_count + row.read_index as usize] = true;
            }
            if let Some(value_index) = row.value_index {
                pooled_references += 1;
                if pooled_references > MAX_POOLED_REFERENCES {
                    return Err(BOUNDS);
                }
                used[read_end + value_index as usize] = true;
            }
            if !matches!(mode, ScanMode::Legacy) && output.is_some() && !row.proves_cells(depth) {
                output = None;
            }
            emit_if_available(&mut output, dense, |out| out.property(&row, ordinal, packet))?;
        }
        emit_if_available(&mut output, dense, |out| out.end_node(packet))?;
    }
    if restored + source.len() != packet.original_size {
        return Err(RESTORED);
    }
    if used.contains(&false) {
        return Err(UNUSED);
    }
    Ok(Scan { output, counts, tail: source })
}

pub(super) struct Prepared<'a> {
    pub(super) text: Vec<u8>,
    pub(super) original: Vec<u8>,
    pub(super) keys: Vec<&'a [u8]>,
    reads: Vec<ReadFragment<'a>>,
    pub(super) counts: (usize, usize),
    pub(super) widths: Vec<u32>,
    pub(super) pool_text: Vec<u8>,
    pub(super) sparse: Option<SparsePlan>,
}

impl<'a> Prepared<'a> {
    pub(super) fn reads(&self) -> impl ExactSizeIterator<Item = &'a [u8]> + '_ {
        self.reads.iter().map(|read| read.raw)
    }
}

pub(super) fn prepare(frame: &[u8], limit: usize) -> Checked<Option<Prepared<'_>>> {
    prepare_version(frame, limit, false, false)
}

pub(super) fn prepare_pooled(frame: &[u8], limit: usize) -> Checked<Option<Prepared<'_>>> {
    prepare_version(frame, limit, true, false)
}

pub(super) fn prepare_sparse(frame: &[u8], limit: usize) -> Checked<Option<Prepared<'_>>> {
    prepare_version(frame, limit, true, true)
}

fn prepare_version(frame: &[u8], limit: usize, pooled: bool, sparse: bool) -> Checked<Option<Prepared<'_>>> {
    let packet = Packet::new(frame, limit, pooled)?;
    let scan = scan(&packet, ScanMode::Dense { sparse })?;
    let Some(Assembly::Dense { mut text, mut original, widths, mut sparse }) = scan.output else {
        return Ok(None);
    };
    let omitted = sparse.as_ref().map_or(0, |plan| plan.omitted);
    if scan.tail.len() > packet.limit - omitted - text.len() {
        return Ok(None);
    }
    append(&mut text, scan.tail, packet.limit)?;
    append(&mut original, scan.tail, packet.original_size)?;
    let mut pool_text = Vec::new();
    if packet.pooled {
        append(&mut pool_text, b"[[[[[", packet.limit)?;
        for (index, value) in packet.values.iter().enumerate() {
            if index != 0 {
                append(&mut pool_text, b",", packet.limit)?;
            }
            append(&mut pool_text, value, packet.limit)?;
        }
        append(&mut pool_text, b"]]]]]", packet.limit)?;
    }
    if let Some(plan) = &mut sparse {
        // A surviving sparse plan has at most MAX_SPARSE_SLOTS rows, and
        // the scan has proved every read entry belongs to one of those rows.
        plan.batch_bounds.try_reserve_exact(packet.reads.len())?;
        plan.covered_groups.try_reserve_exact(packet.reads.len())?;
        let mut groups = HashMap::new();
        groups.try_reserve(MAX_COVERED_GROUPS)?;
        plan.covered_sources.try_reserve(MAX_COVERED_GROUPS)?;
        for read in &packet.reads {
            let mut group = NO_COVERED_GROUP;
            if read.raw[0] == b'f' && read.raw.len() - 9 <= MAX_COVERED_BYTES {
                let source = &read.raw[9..];
                if let Some(existing) = groups.get(source) {
                    group = *existing;
                } else if groups.len() < MAX_COVERED_GROUPS {
                    group = groups.len() as u16;
                    let mut owned = Vec::new();
                    owned.try_reserve_exact(source.len())?;
                    owned.extend_from_slice(source);
                    plan.covered_sources.push(owned);
                    groups.insert(source, group);
                }
            }
            let max_index = if read.raw[0] == b'b' || group != NO_COVERED_GROUP {
                let owner = u32::from_le_bytes(read.raw[1..5].try_into().unwrap());
                let position = u32::from_le_bytes(read.raw[5..9].try_into().unwrap());
                owner.max(position)
            } else {
                u32::MAX
            };
            plan.batch_bounds.push(max_index);
            plan.covered_groups.push(group);
        }
    }
    Ok(Some(Prepared { text, original, keys: packet.keys, reads: packet.reads, counts: scan.counts, widths, pool_text, sparse }))
}

pub(super) fn restore(frame: &[u8], limit: usize) -> Checked<Vec<u8>> {
    restore_version(frame, limit, false)
}

pub(super) fn restore_pooled(frame: &[u8], limit: usize) -> Checked<Vec<u8>> {
    restore_version(frame, limit, true)
}

pub(super) fn restore_original(
    frame: &[u8], limit: usize, pooled: bool, depth: usize,
) -> Checked<Option<(Vec<u8>, Vec<usize>)>> {
    let packet = Packet::new(frame, limit, pooled)?;
    let scan = scan(&packet, ScanMode::Original { depth })?;
    let Some(Assembly::Original { mut text, property_offsets }) = scan.output else {
        return Ok(None);
    };
    append(&mut text, scan.tail, packet.original_size)?;
    Ok(Some((text, property_offsets)))
}

pub(super) struct GapBinding {
    pub(super) start: usize,
    pub(super) end: usize,
    pub(super) read: bool,
}

pub(super) fn restore_segmented_original(
    frame: &[u8], limit: usize, depth: usize,
) -> Checked<Option<(Vec<u8>, Vec<GapBinding>)>> {
    let limit = limit.min(MAX_BYTES);
    if !(CUS2_HEADER..=limit).contains(&frame.len()) {
        return Err(FRAME_SIZE);
    }
    let mut source = frame;
    if take(&mut source, 4)? != b"CUS2" {
        return Err(BOUNDS);
    }
    let literal_size = u32(&mut source)? as usize;
    let key_size = u32(&mut source)? as usize;
    let read_size = u32(&mut source)? as usize;
    let gaps_size = u32(&mut source)? as usize;
    let literal = take(&mut source, literal_size)?;
    let key_pool = take(&mut source, key_size)?;
    let read_pool = take(&mut source, read_size)?;
    if source.len() != gaps_size || gaps_size % 8 != 0 {
        return Err(BOUNDS);
    }
    let gap_count = gaps_size / 8;
    let keys = pool(key_pool, gap_count, Ok)?;
    let reads = pool(read_pool, gap_count - keys.len(), ReadFragment::new)?;
    if !keys.iter().all(|key| one_value_at_depth(key, depth)) || !reads.iter().all(|read| read.proves_value(depth)) {
        return Ok(None);
    }
    let mut used = Vec::new();
    used.try_reserve_exact(keys.len() + reads.len())?;
    used.resize(keys.len() + reads.len(), false);
    let mut bindings = Vec::new();
    bindings.try_reserve_exact(gap_count)?;
    let mut output = Vec::new();
    let mut cursor = 0_usize;
    let mut synthetic_size = literal_size;
    while !source.is_empty() {
        let distance = u32(&mut source)? as usize;
        let tag = u32(&mut source)?;
        let read = tag & 1 == 1;
        let index = (tag >> 1) as usize;
        let end = cursor.checked_add(distance).filter(|&end| end <= literal.len()).ok_or(BOUNDS)?;
        // Python's synthetic frame counts the entire marker spellings,
        // including the delimiters, against the same restored-byte limit.
        synthetic_size += if read { b"\nInfinity\n".len() } else { b"\nNaN\n".len() };
        if synthetic_size > limit {
            return Err(BUDGET);
        }
        append(&mut output, &literal[cursor..end], limit)?;
        let start = output.len();
        if read {
            reads.get(index).ok_or(BOUNDS)?.write_original(&mut output, limit)?;
            used[keys.len() + index] = true;
        } else {
            append(&mut output, keys.get(index).ok_or(BOUNDS)?, limit)?;
            used[index] = true;
        }
        bindings.push(GapBinding { start, end: output.len(), read });
        cursor = end;
    }
    if used.contains(&false) {
        return Err(UNUSED);
    }
    append(&mut output, &literal[cursor..], limit)?;
    Ok(Some((output, bindings)))
}

fn restore_version(frame: &[u8], limit: usize, pooled: bool) -> Checked<Vec<u8>> {
    let packet = Packet::new(frame, limit, pooled)?;
    let scan = scan(&packet, ScanMode::Legacy)?;
    let Some(Assembly::Legacy { mut literal, gaps }) = scan.output else {
        return Err(BOUNDS);
    };
    append(&mut literal, scan.tail, packet.limit)?;
    let sections = [literal.as_slice(), packet.key_pool, packet.read_pool, gaps.bytes.as_slice()];
    let size = CUS2_HEADER + sections.iter().map(|section| section.len()).sum::<usize>();
    if size > packet.limit {
        return Err(FRAME_SIZE);
    }
    let mut frame = Vec::new();
    frame.try_reserve_exact(size)?;
    frame.extend_from_slice(b"CUS2");
    for section in sections {
        frame.extend_from_slice(&(section.len() as u32).to_le_bytes());
    }
    for section in sections {
        frame.extend_from_slice(section);
    }
    Ok(frame)
}

#[cfg(test)]
mod tests;
