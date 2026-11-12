// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Match CPython shortest float spelling and the SDK's finite number format.

use std::fmt::Write;

fn is_decimal_midpoint(value: f64, midpoint: u64, power: i32) -> bool {
    let binary = value.to_bits();
    let exponent = ((binary >> 52) & 2047) as i32;
    let fraction = binary & ((1 << 52) - 1);
    let (significand, exponent) = if exponent == 0 {
        (fraction, -1074)
    } else {
        (fraction | (1 << 52), exponent - 1075)
    };
    let zeroes = significand.trailing_zeros();
    if exponent + zeroes as i32 != power - 1 {
        return false;
    }
    let Some(fives) = 5u64.checked_pow(power.unsigned_abs()) else {
        return false;
    };
    let significand = significand >> zeroes;
    if power >= 0 {
        midpoint.checked_mul(fives) == Some(significand)
    } else {
        significand.checked_mul(fives) == Some(midpoint)
    }
}

fn decimal(value: f64) -> (String, i32) {
    let debug = format!("{value:?}");
    let (mantissa, exponent) = debug.split_once('e').unwrap_or((&debug, "0"));
    let exponent = exponent.parse::<i32>().expect("Rust shortest decimal exponent");
    let mut scale = exponent
        - mantissa
            .split_once('.')
            .map_or(0, |(_, fraction)| fraction.len() as i32);
    let mut digits: String = mantissa.chars().filter(|value| *value != '.').collect();
    digits.drain(..digits.len() - digits.trim_start_matches('0').len());
    while digits.ends_with('0') {
        digits.pop();
        scale += 1;
    }

    // Rust's shortest decimal may choose the upper midpoint spelling. Python
    // chooses the even final digit when two spellings are equally close.
    if digits.as_bytes().last().is_some_and(|last| (last - b'0') % 2 != 0) {
        let significand = digits.parse::<u64>().expect("at most seventeen decimal digits");
        for (alternative, midpoint) in [
            (significand - 1, 2 * significand - 1),
            (significand + 1, 2 * significand + 1),
        ] {
            if alternative != 0 && is_decimal_midpoint(value, midpoint, scale) {
                let spelling = format!("{alternative}e{scale}");
                if spelling.parse::<f64>() == Ok(value) {
                    digits = alternative.to_string();
                    while digits.ends_with('0') {
                        digits.pop();
                        scale += 1;
                    }
                    break;
                }
            }
        }
    }
    (digits, scale)
}

pub(crate) fn float(value: f64, python_repr: bool) -> String {
    if value == 0.0 {
        return if python_repr && value.is_sign_negative() {
            "-0.0"
        } else if python_repr {
            "0.0"
        } else {
            "0"
        }
        .to_owned();
    }
    let negative = value.is_sign_negative();
    let value = value.abs();
    let (digits, scale) = decimal(value);
    let point = digits.len() as i32 + scale;
    let scientific = if python_repr {
        !(-3..=16).contains(&point)
    } else {
        !(1e-6..1e21).contains(&value)
    };
    let mut output = if negative { String::from("-") } else { String::new() };
    if scientific {
        output.push_str(&digits[..1]);
        if digits.len() > 1 {
            output.push('.');
            output.push_str(&digits[1..]);
        }
        let exponent = point - 1;
        let _ = write!(output, "e{}", if exponent < 0 { '-' } else { '+' });
        if python_repr {
            let _ = write!(output, "{:02}", exponent.abs());
        } else {
            let _ = write!(output, "{}", exponent.abs());
        }
    } else if point <= 0 {
        output.push_str("0.");
        output.extend(std::iter::repeat_n('0', point.unsigned_abs() as usize));
        output.push_str(&digits);
    } else if point as usize >= digits.len() {
        output.push_str(&digits);
        output.extend(std::iter::repeat_n('0', point as usize - digits.len()));
        if python_repr {
            output.push_str(".0");
        }
    } else {
        output.push_str(&digits[..point as usize]);
        output.push('.');
        output.push_str(&digits[point as usize..]);
    }
    output
}
