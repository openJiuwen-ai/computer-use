// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Prove one bounded JSON cell without interpreting its Python value.

pub(super) const MAX_DEPTH: usize = 128;

struct Lex<'a>(&'a [u8]);
impl Lex<'_> {
    fn whitespace(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\r' | b'\n')) {
            self.pop();
        }
    }
    fn peek(&self) -> Option<u8> {
        self.0.first().copied()
    }
    fn pop(&mut self) -> Option<u8> {
        let (value, rest) = self.0.split_first()?;
        self.0 = rest;
        Some(*value)
    }
    fn word(&mut self, word: &[u8]) -> bool {
        if !self.0.starts_with(word) {
            return false;
        }
        self.0 = &self.0[word.len()..];
        true
    }
    fn string(&mut self) -> bool {
        if self.pop() != Some(b'"') {
            return false;
        }
        while let Some(byte) = self.pop() {
            match byte {
                b'"' => return true,
                0..=31 => return false,
                b'\\' => match self.pop() {
                    Some(b'u') => {
                        for _ in 0..4 {
                            if !self.pop().is_some_and(|b| b.is_ascii_hexdigit()) {
                                return false;
                            }
                        }
                    }
                    Some(b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't') => (),
                    _ => return false,
                },
                _ => (),
            }
        }
        false
    }
    fn digits(&mut self) -> bool {
        let mut consumed = false;
        while self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
            self.pop();
            consumed = true;
        }
        consumed
    }
    fn number(&mut self) -> bool {
        if self.peek() == Some(b'-') {
            self.pop();
        }
        if self.peek() == Some(b'0') {
            self.pop();
        } else if !self.digits() {
            return false;
        }
        if self.peek() == Some(b'.') {
            self.pop();
            if !self.digits() {
                return false;
            }
        }
        if matches!(self.peek(), Some(b'e' | b'E')) {
            self.pop();
            if matches!(self.peek(), Some(b'+' | b'-')) {
                self.pop();
            }
            if !self.digits() {
                return false;
            }
        }
        true
    }
    fn value(&mut self, depth: usize, maximum_depth: usize) -> bool {
        self.whitespace();
        match self.peek() {
            Some(b'"') => self.string(),
            Some(b'n') => self.word(b"null"),
            Some(b't') => self.word(b"true"),
            Some(b'f') => self.word(b"false"),
            Some(b'-' | b'0'..=b'9') => self.number(),
            Some(b'[' | b'{') if depth < maximum_depth => {
                let object = self.pop() == Some(b'{');
                let close = if object { b'}' } else { b']' };
                self.whitespace();
                if self.peek() == Some(close) {
                    self.pop();
                    return true;
                }
                loop {
                    if object {
                        self.whitespace();
                        if !self.string() {
                            return false;
                        }
                        self.whitespace();
                        if self.pop() != Some(b':') {
                            return false;
                        }
                    }
                    if !self.value(depth + 1, maximum_depth) {
                        return false;
                    }
                    self.whitespace();
                    match self.pop() {
                        Some(value) if value == close => return true,
                        Some(b',') => (),
                        _ => return false,
                    }
                }
            }
            _ => false,
        }
    }
}
pub(super) fn one_value_at_depth(source: &[u8], maximum_depth: usize) -> bool {
    let mut parser = Lex(source);
    if !parser.value(0, maximum_depth) {
        return false;
    }
    parser.whitespace();
    parser.0.is_empty()
}

/// Return the length of the first complete cell in a trusted JSON fragment.
pub(super) fn value_prefix_len(source: &[u8]) -> Option<usize> {
    let mut parser = Lex(source);
    parser.value(0, MAX_DEPTH).then_some(source.len() - parser.0.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn one_value(source: &[u8]) -> bool {
        one_value_at_depth(source, MAX_DEPTH)
    }

    #[test]
    fn proves_exactly_one_bounded_json_value() {
        for valid in [
            b" -0.5e+2 \r\n".as_slice(),
            br#"{"data":["x\"[]", "\uD800"],"data":true}"#,
            b"null",
            b"[]",
        ] {
            assert!(one_value(valid), "{valid:?}");
        }
        for invalid in [b" ".as_slice(), b"1,2", b"[1,]", b"1.", b"01", b"NaN", b"Infinity", br#""\q""#] {
            assert!(!one_value(invalid), "{invalid:?}");
        }
        let mut deep = vec![b'['; MAX_DEPTH + 1];
        deep.push(b'0');
        deep.extend(std::iter::repeat_n(b']', MAX_DEPTH + 1));
        assert!(!one_value(&deep));
        assert!(one_value(&deep[1..deep.len() - 1]));
    }
}
