//! NUMERIC coefficient decoding. Pure Rust.
//!
//! The field is `count` (1, 2, 3 or 4) little-endian 32-bit words, most
//! significant word first, in two's complement. The decimal value is
//! `coefficient * 10^-scale`.

/// Two's-complement coefficient from `count` LE words (count = chunk_len / 4).
pub fn coefficient(words: &[u8], count: usize) -> i128 {
    debug_assert!((1..=4).contains(&count) && words.len() >= count * 4);
    let mut acc: u128 = 0;
    for i in 0..count {
        let w = u32::from_le_bytes([
            words[i * 4],
            words[i * 4 + 1],
            words[i * 4 + 2],
            words[i * 4 + 3],
        ]);
        acc = (acc << 32) | w as u128;
    }
    if count == 4 {
        return acc as i128;
    }
    let bits = 32 * count as u32;
    let top_word = u32::from_le_bytes([words[0], words[1], words[2], words[3]]);
    if top_word >> 31 == 1 {
        acc as i128 - (1i128 << bits)
    } else {
        acc as i128
    }
}

/// Text in the form `Decimal` accepts exactly: `"<coefficient>E-<scale>"`.
/// Kept as the test reference for the allocation-free formatter below.
#[cfg(test)]
pub fn decimal_text(coefficient: i128, scale: i64) -> String {
    format!("{coefficient}E-{scale}")
}

/// Write `"<coefficient>E-<scale>"` into `out` without a heap allocation.
///
/// `scale` is the NUMERIC scale byte (0..=255). The caller passes a buffer of at
/// least [`DECIMAL_TEXT_CAPACITY`] bytes; the returned slice is ASCII.
pub const DECIMAL_TEXT_CAPACITY: usize = 48;

pub fn decimal_text_into(coefficient: i128, scale: i64, out: &mut [u8]) -> usize {
    debug_assert!((0..=255).contains(&scale));
    let mut pos = 0;
    let mut magnitude = if coefficient < 0 {
        out[pos] = b'-';
        pos += 1;
        // wrapping_neg gives 2^127 for i128::MIN, the correct magnitude.
        (coefficient as u128).wrapping_neg()
    } else {
        coefficient as u128
    };

    let mut digits = [0u8; 39];
    let mut count = 0;
    loop {
        digits[count] = b'0' + (magnitude % 10) as u8;
        magnitude /= 10;
        count += 1;
        if magnitude == 0 {
            break;
        }
    }
    for i in (0..count).rev() {
        out[pos] = digits[i];
        pos += 1;
    }

    out[pos] = b'E';
    out[pos + 1] = b'-';
    pos += 2;

    let mut scale = scale as u64;
    let mut scale_digits = [0u8; 3];
    let mut scale_count = 0;
    loop {
        scale_digits[scale_count] = b'0' + (scale % 10) as u8;
        scale /= 10;
        scale_count += 1;
        if scale == 0 {
            break;
        }
    }
    for i in (0..scale_count).rev() {
        out[pos] = scale_digits[i];
        pos += 1;
    }
    pos
}

#[cfg(test)]
mod tests {
    use super::*;

    fn words(vals: &[u32]) -> Vec<u8> {
        vals.iter().flat_map(|w| w.to_le_bytes()).collect()
    }

    #[test]
    fn one_word_is_signed() {
        assert_eq!(coefficient(&words(&[0xFFFF_FFFF]), 1), -1);
        assert_eq!(coefficient(&words(&[0x7FFF_FFFF]), 1), 2_147_483_647);
    }

    #[test]
    fn two_words_are_signed_64_bit() {
        // -1 as 64-bit: both words all ones.
        assert_eq!(coefficient(&words(&[0xFFFF_FFFF, 0xFFFF_FFFF]), 2), -1);
        assert_eq!(coefficient(&words(&[0x8000_0000, 0]), 2), -(1i128 << 63));
    }

    #[test]
    fn three_words_sign_from_most_significant() {
        assert_eq!(
            coefficient(&words(&[0xFFFF_FFFF, 0xFFFF_FFFF, 0xFFFF_FFFE]), 3),
            -2
        );
        assert_eq!(coefficient(&words(&[0x0000_0001, 0, 0]), 3), 1i128 << 64);
    }

    #[test]
    fn four_words_wrap_as_128_bit_two_complement() {
        assert_eq!(coefficient(&words(&[0xFFFF_FFFF; 4]), 4), -1);
        assert_eq!(coefficient(&words(&[0, 0, 0, 7]), 4), 7);
    }

    #[test]
    fn decimal_text_matches_python_fallback_format() {
        assert_eq!(decimal_text(-1234, 2), "-1234E-2");
        assert_eq!(decimal_text(0, 0), "0E-0");
    }

    fn text_into(coefficient: i128, scale: i64) -> String {
        let mut buf = [0u8; DECIMAL_TEXT_CAPACITY];
        let len = decimal_text_into(coefficient, scale, &mut buf);
        std::str::from_utf8(&buf[..len]).unwrap().to_owned()
    }

    #[test]
    fn stack_format_matches_heap_format() {
        for coefficient in [
            0,
            1,
            -1,
            9,
            10,
            -1234,
            i128::MAX,
            i128::MIN,
            10i128.pow(32),
            -(10i128.pow(32)),
        ] {
            for scale in [0, 2, 4, 6, 255] {
                assert_eq!(
                    text_into(coefficient, scale),
                    decimal_text(coefficient, scale),
                    "coefficient={coefficient} scale={scale}"
                );
            }
        }
    }
}
