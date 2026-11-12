// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use std::borrow::Cow;
use std::net::Ipv6Addr;

use super::{WireError, WireResult};

// Match the scheme tables used by the macOS extension's CPython 3.11 runtime.
const RELATIVE_SCHEMES: &[&str] = &[
    "", "ftp", "http", "gopher", "nntp", "imap", "wais", "file", "https", "shttp", "mms",
    "prospero", "rtsp", "rtsps", "rtspu", "sftp", "svn", "svn+ssh", "ws", "wss",
];
const AUTHORITY_SCHEMES: &[&str] = &[
    "", "ftp", "http", "gopher", "nntp", "telnet", "imap", "wais", "file", "mms", "https", "shttp",
    "snews", "prospero", "rtsp", "rtsps", "rtspu", "rsync", "svn", "svn+ssh", "sftp", "nfs", "git",
    "git+ssh", "ws", "wss",
];
const PARAMETER_SCHEMES: &[&str] = &[
    "", "ftp", "hdl", "prospero", "http", "imap", "https", "shttp", "rtsp", "rtsps", "rtspu",
    "sip", "sips", "mms", "sftp", "tel",
];

#[derive(Default)]
struct Parts<'a> {
    scheme: Cow<'a, str>,
    authority: &'a str,
    path: &'a str,
    query: &'a str,
    fragment: &'a str,
}

impl Parts<'_> {
    fn compose(&self, scheme: &str, params: &str) -> String {
        let path = if params.is_empty() {
            Cow::Borrowed(self.path)
        } else {
            Cow::Owned(format!("{};{params}", self.path))
        };
        let mut url = String::new();
        if !scheme.is_empty() {
            url.push_str(scheme);
            url.push(':');
        }
        if !self.authority.is_empty()
            || (!scheme.is_empty() && AUTHORITY_SCHEMES.contains(&scheme))
            || path.starts_with("//")
        {
            url.push_str("//");
            url.push_str(self.authority);
            if !path.is_empty() && !path.starts_with('/') {
                url.push('/');
            }
        }
        url.push_str(&path);
        if !self.query.is_empty() {
            url.push('?');
            url.push_str(self.query);
        }
        if !self.fragment.is_empty() {
            url.push('#');
            url.push_str(self.fragment);
        }
        url
    }
}

pub(super) fn resolve_url(base: &str, reference: &str) -> WireResult<String> {
    let clean_base = clean(base);
    let parsed_base = split(&clean_base)?;
    // wire.py resolves relative paths under HTTP rules for any base scheme,
    // then restores that scheme. In particular this covers custom app URLs.
    if !parsed_base.scheme.is_empty() && split(&clean(reference))?.scheme.is_empty() {
        let joined = join(&parsed_base.compose("http", ""), reference)?;
        let clean_joined = clean(&joined);
        return Ok(split(&clean_joined)?.compose(&parsed_base.scheme, ""));
    }
    join(base, reference)
}

fn clean(url: &str) -> Cow<'_, str> {
    let url = url.trim_start_matches(|ch| ch <= '\u{20}');
    if url.contains(['\t', '\r', '\n']) {
        Cow::Owned(url.replace(['\t', '\r', '\n'], ""))
    } else {
        Cow::Borrowed(url)
    }
}

fn split(mut url: &str) -> WireResult<Parts<'_>> {
    let mut parts = Parts::default();
    if let Some((scheme, rest)) = url.split_once(':') {
        if scheme
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphabetic)
            && scheme
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"+-.".contains(&byte))
        {
            parts.scheme = if scheme.bytes().any(|byte| byte.is_ascii_uppercase()) {
                Cow::Owned(scheme.to_ascii_lowercase())
            } else {
                Cow::Borrowed(scheme)
            };
            url = rest;
        }
    }
    if let Some(rest) = url.strip_prefix("//") {
        let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
        (parts.authority, url) = rest.split_at(end);
        check_authority(parts.authority)?;
    }
    (url, parts.fragment) = url.split_once('#').unwrap_or((url, ""));
    (parts.path, parts.query) = url.split_once('?').unwrap_or((url, ""));
    Ok(parts)
}

fn check_authority(authority: &str) -> WireResult<()> {
    // CPython 3.11 uses Unicode 14.0. These are the non-ASCII scalars whose
    // NFKC forms contain / ? # @ or :. None of those ASCII delimiters has a
    // canonical composition, so neighboring scalars cannot hide a delimiter.
    if authority.chars().any(|scalar| matches!(scalar,
        '\u{2047}'..='\u{2049}' | '\u{2100}' | '\u{2101}' | '\u{2105}' | '\u{2106}' | '\u{2a74}'
        | '\u{fe13}' | '\u{fe16}' | '\u{fe55}' | '\u{fe56}' | '\u{fe5f}' | '\u{fe6b}'
        | '\u{ff03}' | '\u{ff0f}' | '\u{ff1a}' | '\u{ff1f}' | '\u{ff20}'
    )) {
        return Err(WireError::Invalid("relative URL normalized authority delimiter"));
    }
    let left = authority.contains('[');
    let right = authority.contains(']');
    let invalid = WireError::Invalid("relative URL bracketed authority");
    if left != right {
        return Err(invalid);
    }
    if !left {
        return Ok(());
    }
    let host_and_port = authority.rsplit('@').next().unwrap_or("");
    let host = match host_and_port.split_once('[') {
        Some(("", address)) => {
            let (host, port) = address.split_once(']').unwrap_or((address, ""));
            if !port.is_empty() && !port.starts_with(':') {
                return Err(invalid);
            }
            host
        }
        Some(_) => return Err(invalid),
        None => host_and_port.split(':').next().unwrap_or(""),
    };
    if let Some(future) = host.strip_prefix('v') {
        let valid = future.split_once('.').is_some_and(|(version, address)| {
            !version.is_empty()
                && version.bytes().all(|byte| byte.is_ascii_hexdigit())
                && !address.is_empty()
        });
        return if valid { Ok(()) } else { Err(invalid) };
    }
    let address = match host.split_once('%') {
        Some((address, scope)) if !scope.is_empty() && !scope.contains('%') => address,
        Some(_) => return Err(invalid),
        None => host,
    };
    address.parse::<Ipv6Addr>().map(|_| ()).map_err(|_| invalid)
}

fn path_and_params<'a>(path: &'a str, scheme: &str) -> (&'a str, &'a str) {
    if PARAMETER_SCHEMES.contains(&scheme) {
        let last_slash = path.rfind('/').unwrap_or(0);
        if let Some(offset) = path[last_slash..].find(';') {
            let index = last_slash + offset;
            return (&path[..index], &path[index + 1..]);
        }
    }
    (path, "")
}

fn join(base: &str, reference: &str) -> WireResult<String> {
    if base.is_empty() {
        return Ok(reference.to_owned());
    }
    if reference.is_empty() {
        return Ok(base.to_owned());
    }
    let clean_base = clean(base);
    let clean_reference = clean(reference);
    let base = split(&clean_base)?;
    let mut parts = split(&clean_reference)?;
    let scheme = if parts.scheme.is_empty() {
        &base.scheme
    } else {
        &parts.scheme
    };
    if *scheme != base.scheme || !RELATIVE_SCHEMES.contains(&scheme.as_ref()) {
        return Ok(reference.to_owned());
    }
    let (base_path, base_params) = path_and_params(base.path, scheme);
    let (path, params) = path_and_params(parts.path, scheme);
    parts.path = path;
    if AUTHORITY_SCHEMES.contains(&scheme.as_ref()) {
        if !parts.authority.is_empty() {
            return Ok(parts.compose(scheme, params));
        }
        parts.authority = base.authority;
    }
    if path.is_empty() && params.is_empty() {
        parts.path = base_path;
        if parts.query.is_empty() {
            parts.query = base.query;
        }
        return Ok(parts.compose(scheme, base_params));
    }
    let path = join_path(base_path, path);
    parts.path = &path;
    Ok(parts.compose(scheme, params))
}

fn join_path(base: &str, path: &str) -> String {
    let mut segments = Vec::new();
    let absolute = path.starts_with('/');
    if !absolute {
        segments.extend(base.split('/'));
        if segments.last() != Some(&"") {
            segments.pop();
        }
    }
    segments.extend(path.split('/'));
    let trailing = matches!(segments.last(), Some(&"." | &".."));
    let last = segments.len() - 1;
    let mut resolved = Vec::new();
    for (index, segment) in segments.into_iter().enumerate() {
        match segment {
            "." => (),
            ".." => {
                resolved.pop();
            }
            "" if !absolute && index != 0 && index != last => (),
            segment => resolved.push(segment),
        }
    }
    if trailing {
        resolved.push("");
    }
    let joined = resolved.join("/");
    if joined.is_empty() {
        "/".to_owned()
    } else {
        joined
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_python_311_relative_cfurl_values() {
        const FILE: &str = "file:///tmp/parent/";
        const HTTP: &str = "https://example.test/a/b";
        const QUERY: &str = "https://EXAMPLE.test:443/first//second;params?old#base";
        const CUSTOM: &str = "com.example+app://HOST/base/index";
        // Expected values from the exact urllib.parse sequence in wire.py.
        let cases = [
            (FILE, "../item%20one", "file:///tmp/item%20one"),
            (
                FILE,
                "/absolute//dir/%2e%2e/",
                "file:///absolute//dir/%2e%2e/",
            ),
            (
                "file://Volumes/share/dir/file",
                "../../Other/./foo?q=%2F#slice",
                "file://Volumes/Other/foo?q=%2F#slice",
            ),
            (
                "FILE://Volumes/share/dir/file",
                "子/文😀",
                "file://Volumes/share/dir/子/文😀",
            ),
            ("file:relative/a", "../out", "file:///out"),
            (
                QUERY,
                "../x//y;keep?q=%2F#part",
                "https://EXAMPLE.test:443/x/y;keep?q=%2F#part",
            ),
            (
                QUERY,
                "#anchor",
                "https://EXAMPLE.test:443/first//second;params?old#anchor",
            ),
            (
                QUERY,
                "?",
                "https://EXAMPLE.test:443/first//second;params?old",
            ),
            (QUERY, "", QUERY),
            (HTTP, ";params", "https://example.test/a/;params"),
            (HTTP, "../../../..", "https://example.test/"),
            (
                HTTP,
                "//alice:pass@HOST:9/./item",
                "https://alice:pass@HOST:9/./item",
            ),
            (
                HTTP,
                "//[2001:db8::ffff:192.0.2.10]:80/./item",
                "https://[2001:db8::ffff:192.0.2.10]:80/./item",
            ),
            (HTTP, "//[fe80::1%25en0]/", "https://[fe80::1%25en0]/"),
            (HTTP, "//[vA.address]:9/", "https://[vA.address]:9/"),
            (HTTP, "HTTPS:../c?z#n", "https://example.test/c?z#n"),
            (HTTP, "  FTP://Else/p?#", "  FTP://Else/p?#"),
            (HTTP, "\t \nnext\r\t%2F ", "https://example.test/a/next%2F "),
            (
                CUSTOM,
                "/menu//entry?x=1#anchor",
                "com.example+app://HOST/menu//entry?x=1#anchor",
            ),
            (CUSTOM, "//ALT/./item", "com.example+app://ALT/./item"),
            (
                "com.example+app:/base/index",
                "../sheet",
                "com.example+app:/sheet",
            ),
            (
                "com.example+app:base/index",
                "?q=x",
                "com.example+app:/base/index?q=x",
            ),
            (
                CUSTOM,
                "COM.EXAMPLE+APP:../outside",
                "COM.EXAMPLE+APP:../outside",
            ),
            (CUSTOM, "file:///tmp/open?", "file:///tmp/open?"),
            ("relative/base?old#before", "./next/", "relative/next/"),
            ("relative/base?old#before", "?new", "relative/base?new"),
            ("", "//[malformed", "//[malformed"),
            (HTTP, "//xn--r8jz45g/path", "https://xn--r8jz45g/path"),
        ];
        for (base, reference, expected) in cases {
            assert_eq!(
                resolve_url(base, reference).as_deref(),
                Ok(expected),
                "{base:?} {reference:?}"
            );
        }
    }

    #[test]
    fn matches_python_nfkc_authority_validation_without_rewriting_the_hostname() {
        assert_eq!(resolve_url("https://例子.test/a", "path").as_deref(), Ok("https://例子.test/path"));
        assert_eq!(
            resolve_url("app://u\u{301}@例子.test/a/", "../path").as_deref(),
            Ok("app://u\u{301}@例子.test/path")
        );
        assert_eq!(
            resolve_url("https://example.test/a", "//a：b/path"),
            Err(WireError::Invalid("relative URL normalized authority delimiter"))
        );
        assert!(matches!(
            resolve_url("https://example.test/a", "//[1.2.3.4]/path"),
            Err(WireError::Invalid(_))
        ));
    }
}
