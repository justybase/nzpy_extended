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
pub fn decimal_text(coefficient: i128, scale: i64) -> String {
    format!("{coefficient}E-{scale}")
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
}
