//! Parsing and formatting of official NOAA SPC & WPC outlook text discussions.

use crate::format_rfc3339_utc;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use time::{Date, Month, OffsetDateTime, PrimitiveDateTime, Time, UtcOffset};

const MAX_HEADLINE_CHARS: usize = 550;
const MAX_SUMMARY_CHARS: usize = 340;
const MAX_FIRST_SENTENCE_CHARS: usize = 500;
const MAX_SECTION_HEADING_CHARS: usize = 180;
const MAX_SECTION_BODY_CHARS: usize = 11_500;
const MAX_SECTIONS: usize = 16;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DiscussionSection {
    pub heading: String,
    pub body: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OutlookDiscussion {
    pub headline: String,
    pub summary: String,
    pub sections: Vec<DiscussionSection>,
    pub issued: Option<String>,
    pub source_url: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ParsedWpcDayDiscussion {
    pub day: u8,
    pub valid_from: Option<OffsetDateTime>,
    pub valid_to: Option<OffsetDateTime>,
    pub issued: Option<OffsetDateTime>,
    pub discussion: OutlookDiscussion,
}

pub fn cap_text(text: &str, max_chars: usize) -> String {
    let trimmed = text.trim();
    if trimmed.chars().count() <= max_chars {
        return trimmed.to_owned();
    }
    let keep = max_chars.saturating_sub(1);
    let mut out: String = trimmed.chars().take(keep).collect();
    if let Some(space) = out.rfind(' ') {
        if space > keep / 2 {
            out.truncate(space);
        }
    }
    out.push('…');
    out
}

/// Reflow NOAA 68-column hard-wrapped paragraphs into natural prose paragraphs.
pub fn unwrap_paragraphs(raw: &str) -> String {
    let normalized = raw.replace("\r\n", "\n").replace('\r', "\n");
    let mut paragraphs = Vec::new();
    let mut current = Vec::new();
    for line in normalized.lines() {
        let trimmed = line.split_whitespace().collect::<Vec<_>>().join(" ");
        if trimmed.is_empty() {
            if !current.is_empty() {
                paragraphs.push(current.join(" "));
                current.clear();
            }
        } else {
            current.push(trimmed);
        }
    }
    if !current.is_empty() {
        paragraphs.push(current.join(" "));
    }
    paragraphs.join("\n\n")
}

fn is_abbreviation_ending(prefix: &str) -> bool {
    let lower = prefix.to_ascii_lowercase();
    const ABBREVS: &[&str] = &[
        "u.s.", "st.", "mt.", "vs.", "approx.", "e.g.", "i.e.", "a.m.", "p.m.", "dr.", "mr.",
        "ms.", "jr.", "sr.", "no.", "fig.", "est.", "max.", "min.", "ave.", "blvd.", "hwy.",
    ];
    ABBREVS.iter().any(|abbr| lower.ends_with(abbr))
}

/// Extract the first 1-2 complete sentences from unwrapped prose.
pub fn extract_summary_sentences(text: &str, max_chars: usize) -> String {
    let single_space = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if single_space.is_empty() {
        return String::new();
    }
    let bytes = single_space.as_bytes();
    let mut sentence_ends = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        let ch = bytes[i];
        if matches!(ch, b'.' | b'!' | b'?') {
            let prev_digit = i > 0 && bytes[i - 1].is_ascii_digit();
            let next_digit = i + 1 < bytes.len() && bytes[i + 1].is_ascii_digit();
            let decimal_dot = ch == b'.' && prev_digit && next_digit;
            let ellipsis = ch == b'.'
                && ((i + 1 < bytes.len() && bytes[i + 1] == b'.')
                    || (i > 0 && bytes[i - 1] == b'.'));
            let end_pos = i + 1;
            let at_boundary = end_pos == bytes.len() || bytes[end_pos] == b' ';
            if !decimal_dot && !ellipsis && at_boundary {
                let prefix = &single_space[..end_pos];
                if !is_abbreviation_ending(prefix) {
                    sentence_ends.push(end_pos);
                    if sentence_ends.len() == 2 {
                        break;
                    }
                }
            }
        }
        i += 1;
    }
    if let Some(&first_end) = sentence_ends.first() {
        if let Some(&second_end) = sentence_ends.get(1) {
            if single_space[..second_end].chars().count() <= max_chars {
                return single_space[..second_end].to_owned();
            }
        }
        let first_len = single_space[..first_end].chars().count();
        if first_len <= MAX_FIRST_SENTENCE_CHARS {
            return single_space[..first_end].to_owned();
        }
    }
    cap_text(&single_space, max_chars)
}

/// Strip minimal HTML wrapper tags from WPC discussion pages.
pub fn extract_pre_text(raw: &str) -> String {
    let lower = raw.to_ascii_lowercase();
    let content = if let Some(start) = lower.find("<pre") {
        let after_tag = raw[start..].find('>').map_or(start, |gt| start + gt + 1);
        let end = lower[after_tag..]
            .find("</pre>")
            .map_or(raw.len(), |rel| after_tag + rel);
        &raw[after_tag..end]
    } else {
        raw
    };
    let mut stripped = String::with_capacity(content.len());
    let mut in_tag = false;
    for ch in content.chars() {
        match ch {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => stripped.push(ch),
            _ => {}
        }
    }
    stripped
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
}

fn parse_month(token: &str) -> Option<Month> {
    match token.to_ascii_lowercase().as_str() {
        "jan" | "january" => Some(Month::January),
        "feb" | "february" => Some(Month::February),
        "mar" | "march" => Some(Month::March),
        "apr" | "april" => Some(Month::April),
        "may" => Some(Month::May),
        "jun" | "june" => Some(Month::June),
        "jul" | "july" => Some(Month::July),
        "aug" | "august" => Some(Month::August),
        "sep" | "sept" | "september" => Some(Month::September),
        "oct" | "october" => Some(Month::October),
        "nov" | "november" => Some(Month::November),
        "dec" | "december" => Some(Month::December),
        _ => None,
    }
}

fn tz_offset_hours(tz: &str) -> Option<i8> {
    match tz.to_ascii_uppercase().as_str() {
        "UTC" | "GMT" | "Z" => Some(0),
        "EST" => Some(-5),
        "EDT" | "AST" => Some(-4),
        "CST" => Some(-6),
        "CDT" => Some(-5),
        "MST" => Some(-7),
        "MDT" => Some(-6),
        "PST" => Some(-8),
        "PDT" => Some(-7),
        "AKST" => Some(-9),
        "AKDT" => Some(-8),
        _ => None,
    }
}

/// Parse NWS bulletin header timestamp lines like `1159 PM CST Tue Nov 24 2026`.
pub fn parse_nws_local_time(line: &str) -> Option<OffsetDateTime> {
    let tokens: Vec<&str> = line.split_whitespace().collect();
    if tokens.len() != 7 {
        return None;
    }
    let hhmm = tokens[0];
    if !(3..=4).contains(&hhmm.len()) || !hhmm.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let split = hhmm.len() - 2;
    let mut hour: u8 = hhmm[..split].parse().ok()?;
    let minute: u8 = hhmm[split..].parse().ok()?;
    match tokens[1].to_ascii_uppercase().as_str() {
        "AM" => {
            if hour == 12 {
                hour = 0;
            }
        }
        "PM" => {
            if hour != 12 {
                hour = hour.checked_add(12)?;
            }
        }
        _ => return None,
    }
    let offset_hours = tz_offset_hours(tokens[2])?;
    let month = parse_month(tokens[4])?;
    let day: u8 = tokens[5].parse().ok()?;
    let year: i32 = tokens[6].parse().ok()?;
    let date = Date::from_calendar_date(year, month, day).ok()?;
    let time = Time::from_hms(hour, minute, 0).ok()?;
    let offset = UtcOffset::from_hms(offset_hours, 0, 0).ok()?;
    Some(
        PrimitiveDateTime::new(date, time)
            .assume_offset(offset)
            .to_offset(UtcOffset::UTC),
    )
}

fn parse_wpc_valid_endpoint(part: &str) -> Option<OffsetDateTime> {
    let tokens: Vec<&str> = part.split_whitespace().collect();
    if tokens.len() < 4 {
        return None;
    }
    let z_token = tokens[0].trim_end_matches('Z').trim_end_matches('z');
    let (hour, minute): (u8, u8) = match z_token.len() {
        1 | 2 => (z_token.parse().ok()?, 0),
        4 => (z_token[..2].parse().ok()?, z_token[2..].parse().ok()?),
        _ => return None,
    };
    let tail = &tokens[1..];
    let (month_tok, day_tok, year_tok) = match tail {
        [_, m, d, y] => (*m, *d, *y),
        [m, d, y] => (*m, *d, *y),
        _ => return None,
    };
    let month = parse_month(month_tok)?;
    let day: u8 = day_tok.parse().ok()?;
    let year: i32 = year_tok.parse().ok()?;
    let date = Date::from_calendar_date(year, month, day).ok()?;
    let time = Time::from_hms(hour, minute, 0).ok()?;
    Some(OffsetDateTime::new_utc(date, time))
}

/// Parse `Valid 12Z Wed Nov 25 2026 - 12Z Thu Nov 26 2026`.
pub fn parse_wpc_valid_range(line: &str) -> Option<(OffsetDateTime, OffsetDateTime)> {
    let trimmed = line.trim();
    let rest = trimmed
        .strip_prefix("Valid ")
        .or_else(|| trimmed.strip_prefix("VALID "))?;
    let (left, right) = rest.split_once(" - ")?;
    Some((
        parse_wpc_valid_endpoint(left)?,
        parse_wpc_valid_endpoint(right)?,
    ))
}

fn is_spc_signature_line(trimmed: &str) -> bool {
    if trimmed == "$$" || !trimmed.starts_with("..") {
        return false;
    }
    if let Some(after) = trimmed[2..].find("..") {
        let name = trimmed[2..2 + after].trim();
        let rest = trimmed[2 + after + 2..].trim();
        return !name.is_empty() && (rest.is_empty() || rest.contains('/'));
    }
    false
}

fn push_section(sections: &mut Vec<DiscussionSection>, heading: String, raw_body: &str) {
    if sections.len() >= MAX_SECTIONS {
        return;
    }
    let body = unwrap_paragraphs(raw_body);
    let heading = cap_text(&heading, MAX_SECTION_HEADING_CHARS);
    if !heading.is_empty() && !body.is_empty() {
        sections.push(DiscussionSection {
            heading,
            body: cap_text(&body, MAX_SECTION_BODY_CHARS),
        });
    }
}

/// Parse an SPC Day 1/2/3 plain-text convective outlook (`day{1,2,3}otlk.txt`).
pub fn parse_spc_discussion(
    raw: &str,
    day: u8,
    issued_fallback: Option<String>,
) -> Option<OutlookDiscussion> {
    let normalized = raw.replace("\r\n", "\n").replace('\r', "\n");
    let mut issued: Option<OffsetDateTime> = None;
    let mut lines: Vec<&str> = Vec::new();
    for line in normalized.lines() {
        let trimmed = line.trim();
        if trimmed == "$$" || is_spc_signature_line(trimmed) {
            break;
        }
        if issued.is_none() {
            issued = parse_nws_local_time(trimmed);
        }
        lines.push(line);
    }

    let mut headline = String::new();
    let mut summary = String::new();
    let mut sections: Vec<DiscussionSection> = Vec::new();
    let mut current_heading: Option<String> = None;
    let mut current_body: Vec<&str> = Vec::new();

    let mut idx = 0;
    while idx < lines.len() {
        let trimmed = lines[idx].trim();
        if trimmed.starts_with("...") && !trimmed.starts_with("....") {
            if let Some(heading) = current_heading.take() {
                let raw_section = current_body.join("\n");
                if heading.eq_ignore_ascii_case("SUMMARY") {
                    summary = unwrap_paragraphs(&raw_section);
                } else {
                    push_section(&mut sections, heading, &raw_section);
                }
                current_body.clear();
            }

            let mut header_parts = vec![trimmed];
            while !lines[idx].trim().ends_with("...") && idx + 1 < lines.len() {
                let next_trimmed = lines[idx + 1].trim();
                if next_trimmed.is_empty() || next_trimmed.starts_with("...") {
                    break;
                }
                idx += 1;
                header_parts.push(next_trimmed);
                if next_trimmed.ends_with("...") {
                    break;
                }
            }
            let joined = header_parts.join(" ");
            let label = joined.trim_matches('.').trim();
            if headline.is_empty() && !label.eq_ignore_ascii_case("SUMMARY") {
                headline = unwrap_paragraphs(label);
            } else {
                current_heading = Some(label.to_owned());
            }
        } else if current_heading.is_some() {
            current_body.push(lines[idx]);
        }
        idx += 1;
    }
    if let Some(heading) = current_heading {
        let raw_section = current_body.join("\n");
        if heading.eq_ignore_ascii_case("SUMMARY") {
            summary = unwrap_paragraphs(&raw_section);
        } else {
            push_section(&mut sections, heading, &raw_section);
        }
    }

    if summary.is_empty() {
        summary = sections
            .first()
            .map(|section| extract_summary_sentences(&section.body, MAX_SUMMARY_CHARS))
            .unwrap_or_else(|| headline.clone());
    }
    if headline.is_empty() && summary.is_empty() {
        return None;
    }
    Some(OutlookDiscussion {
        headline: cap_text(&headline, MAX_HEADLINE_CHARS),
        summary: cap_text(&summary, 1_450),
        sections,
        issued: issued.map(format_rfc3339_utc).or(issued_fallback),
        source_url: format!("https://www.spc.noaa.gov/products/outlook/day{day}otlk.html"),
    })
}

fn is_wpc_forecaster_line(trimmed: &str) -> bool {
    if is_spc_signature_line(trimmed) {
        return true;
    }
    if trimmed.is_empty()
        || trimmed.len() > 36
        || trimmed.contains('.')
        || trimmed.contains(',')
        || trimmed.contains(':')
        || trimmed.contains('!')
        || trimmed.contains('?')
        || trimmed.bytes().any(|b| b.is_ascii_digit())
    {
        return false;
    }
    let words: Vec<&str> = trimmed.split_whitespace().collect();
    !words.is_empty()
        && words.len() <= 3
        && words
            .iter()
            .all(|w| w.chars().next().is_some_and(|c| c.is_ascii_uppercase()))
}

fn strip_wpc_footer(lines: &mut Vec<&str>) {
    while let Some(&last) = lines.last() {
        let trimmed = last.trim();
        if trimmed.is_empty()
            || trimmed == "$$"
            || trimmed.starts_with("Day 1 threat area:")
            || trimmed.starts_with("Day 2 threat area:")
            || trimmed.starts_with("Day 3 threat area:")
            || trimmed.contains("epoints.txt")
        {
            lines.pop();
            continue;
        }
        let preceded_by_blank = lines
            .len()
            .checked_sub(2)
            .and_then(|i| lines.get(i))
            .is_some_and(|line| line.trim().is_empty());
        if preceded_by_blank && is_wpc_forecaster_line(trimmed) {
            lines.pop();
            continue;
        }
        break;
    }
}

fn looks_like_wpc_risk_headline(text: &str) -> bool {
    let upper = text.to_ascii_uppercase();
    upper.contains("RISK OF EXCESSIVE RAINFALL")
        || upper.contains("LESS THAN 5 PERCENT")
        || upper.contains("LESS THAN 5%")
}

fn parse_wpc_day_block(
    day: u8,
    block_lines: &[&str],
    issued: Option<OffsetDateTime>,
    source_url: &str,
) -> Option<ParsedWpcDayDiscussion> {
    let mut valid_from = None;
    let mut valid_to = None;
    let mut body_start = 0;
    for (idx, line) in block_lines.iter().take(6).enumerate() {
        if let Some((vf, vt)) = parse_wpc_valid_range(line) {
            valid_from = Some(vf);
            valid_to = Some(vt);
            body_start = idx + 1;
            break;
        }
    }

    let mut lines: Vec<&str> = block_lines[body_start..].to_vec();
    strip_wpc_footer(&mut lines);

    let mut headline = String::new();
    let mut sections: Vec<DiscussionSection> = Vec::new();
    let mut general_lines: Vec<&str> = Vec::new();
    let mut current_heading: Option<String> = None;
    let mut current_body: Vec<&str> = Vec::new();

    let mut idx = 0;
    while idx < lines.len() {
        let trimmed = lines[idx].trim();
        if trimmed.starts_with("...") && !trimmed.starts_with("....") {
            let mut header_parts = vec![trimmed];
            while idx + 1 < lines.len() {
                let current = lines[idx].trim();
                if current.len() > 3 && current.ends_with("...") {
                    break;
                }
                let next_trimmed = lines[idx + 1].trim();
                if next_trimmed.is_empty() || next_trimmed.starts_with("...") {
                    break;
                }
                idx += 1;
                header_parts.push(next_trimmed);
                if next_trimmed.ends_with("...") || next_trimmed.ends_with('.') {
                    break;
                }
            }
            let joined = header_parts.join(" ");
            let stripped = joined
                .trim_start_matches('.')
                .trim_end_matches("...")
                .trim_end_matches('.')
                .trim();
            if headline.is_empty() && looks_like_wpc_risk_headline(stripped) {
                headline = unwrap_paragraphs(stripped);
            } else {
                if !general_lines.is_empty() {
                    push_section(&mut sections, "Overview".into(), &general_lines.join("\n"));
                    general_lines.clear();
                }
                if let Some(heading) = current_heading.take() {
                    push_section(&mut sections, heading, &current_body.join("\n"));
                    current_body.clear();
                }
                current_heading = Some(stripped.to_owned());
            }
        } else if current_heading.is_some() {
            current_body.push(lines[idx]);
        } else {
            general_lines.push(lines[idx]);
        }
        idx += 1;
    }

    if let Some(heading) = current_heading {
        push_section(&mut sections, heading, &current_body.join("\n"));
    } else if !general_lines.is_empty() {
        let general_unwrapped = unwrap_paragraphs(&general_lines.join("\n"));
        if !general_unwrapped.is_empty() {
            let mut paras: Vec<&str> = general_unwrapped.split("\n\n").collect();
            if headline.is_empty() && looks_like_wpc_risk_headline(paras[0]) {
                headline = paras.remove(0).to_owned();
            }
            let remaining = paras.join("\n\n");
            if !remaining.is_empty() {
                push_section(&mut sections, "Discussion".into(), &remaining);
            }
        }
    }

    if headline.is_empty() && sections.is_empty() {
        return None;
    }
    let summary = sections
        .first()
        .map(|section| extract_summary_sentences(&section.body, MAX_SUMMARY_CHARS))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| headline.clone());
    Some(ParsedWpcDayDiscussion {
        day,
        valid_from,
        valid_to,
        issued,
        discussion: OutlookDiscussion {
            headline: cap_text(&headline, MAX_HEADLINE_CHARS),
            summary: cap_text(&summary, 1_450),
            sections,
            issued: issued.map(format_rfc3339_utc),
            source_url: source_url.to_owned(),
        },
    })
}

fn day_heading(trimmed: &str) -> Option<u8> {
    match trimmed.to_ascii_lowercase().as_str() {
        "day 1" => Some(1),
        "day 2" => Some(2),
        "day 3" => Some(3),
        _ => None,
    }
}

/// Parse all Day 1-3 blocks from WPC `QPFERD` plain text or HTML wrapper.
pub fn parse_wpc_qpferd(raw: &str) -> HashMap<u8, ParsedWpcDayDiscussion> {
    let text = extract_pre_text(raw)
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    let mut issued: Option<OffsetDateTime> = None;
    let lines: Vec<&str> = text.lines().collect();
    for line in lines.iter().take(20) {
        if issued.is_none() {
            issued = parse_nws_local_time(line.trim());
        }
    }

    let mut markers: Vec<(usize, Option<u8>)> = Vec::new();
    for (idx, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        let lower = trimmed.to_ascii_lowercase();
        if let Some(day) = day_heading(trimmed) {
            markers.push((idx, Some(day)));
        } else if lower.starts_with("day 4")
            || lower.starts_with("day 1 threat area:")
            || trimmed == "$$"
        {
            markers.push((idx, None));
        }
    }

    let mut out = HashMap::new();
    for pos in 0..markers.len() {
        let (start_idx, Some(day)) = markers[pos] else {
            continue;
        };
        let end_idx = markers.get(pos + 1).map_or(lines.len(), |&(i, _)| i);
        let source_url = format!("https://www.wpc.ncep.noaa.gov/qpf/ero.php?opt=curr&day={day}");
        if let Some(parsed) =
            parse_wpc_day_block(day, &lines[start_idx + 1..end_idx], issued, &source_url)
        {
            out.insert(day, parsed);
        }
    }
    out
}

/// Parse a single WPC `ero.php` page (`opt=prev&day=1` or `opt=curr&day=N`).
pub fn parse_wpc_ero_page(raw: &str, day: u8, source_url: &str) -> Option<ParsedWpcDayDiscussion> {
    let text = extract_pre_text(raw)
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    let lines: Vec<&str> = text.lines().collect();
    let mut issued: Option<OffsetDateTime> = None;
    let mut start_idx = None;
    for (idx, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        if issued.is_none() {
            issued = parse_nws_local_time(trimmed);
        }
        if day_heading(trimmed) == Some(day) {
            start_idx = Some(idx + 1);
            break;
        }
    }
    let start = start_idx?;
    parse_wpc_day_block(day, &lines[start..], issued, source_url)
}

#[derive(Clone, Debug)]
struct QpfhsdSection {
    heading: String,
    days: [bool; 3],
    day_label: Option<String>,
    callout: Option<String>,
    body_lines: Vec<String>,
}

fn parse_qpfhsd_day_tag(trimmed: &str) -> Option<([bool; 3], String)> {
    let clean = trimmed.trim_end_matches('.').trim_end_matches(':').trim();
    if clean.is_empty() || clean.len() > 40 {
        return None;
    }
    let lower = clean.to_ascii_lowercase();
    if !lower.contains("day")
        || lower.contains("day 4")
        || lower.contains("day 5")
        || lower.contains('%')
    {
        return None;
    }
    let has1 = clean.contains('1');
    let has2 = clean.contains('2');
    let has3 = clean.contains('3');
    let days = if has1 && has3 {
        [true, true, true]
    } else if has1 && has2 {
        [true, true, false]
    } else if has2 && has3 {
        [false, true, true]
    } else if has1 {
        [true, false, false]
    } else if has2 {
        [false, true, false]
    } else if has3 {
        [false, false, true]
    } else {
        return None;
    };
    let label = clean
        .replace("1-2", "1–2")
        .replace("2-3", "2–3")
        .replace("1-3", "1–3");
    Some((days, label))
}

/// Parse WPC Probabilistic Heavy Snow and Icing Discussion (`QPFHSD`).
pub fn parse_wpc_qpfhsd(
    raw: &str,
    day: Option<u8>,
    source_url: &str,
) -> Option<(
    Option<OffsetDateTime>,
    Option<OffsetDateTime>,
    OutlookDiscussion,
)> {
    let text = extract_pre_text(raw)
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    let all_lines: Vec<&str> = text.lines().collect();
    let mut issued: Option<OffsetDateTime> = None;
    let mut valid_from: Option<OffsetDateTime> = None;
    let mut valid_to: Option<OffsetDateTime> = None;
    let mut body_start = 0;

    for (idx, line) in all_lines.iter().take(24).enumerate() {
        let trimmed = line.trim();
        if issued.is_none() {
            issued = parse_nws_local_time(trimmed);
        }
        if let Some((vf, vt)) = parse_wpc_valid_range(trimmed) {
            valid_from = Some(vf);
            valid_to = Some(vt);
            body_start = idx + 1;
            break;
        }
    }

    let mut lines: Vec<&str> = all_lines[body_start..].to_vec();
    strip_wpc_footer(&mut lines);

    let mut parsed_sections: Vec<QpfhsdSection> = Vec::new();
    let mut general_lines: Vec<&str> = Vec::new();
    let mut current: Option<QpfhsdSection> = None;

    let mut idx = 0;
    while idx < lines.len() {
        let trimmed = lines[idx].trim();
        if trimmed.starts_with("...") && !trimmed.starts_with("....") {
            let mut parts = vec![trimmed];
            while idx + 1 < lines.len() {
                let cur = lines[idx].trim();
                if cur.len() > 3 && cur.ends_with("...") {
                    break;
                }
                let next_t = lines[idx + 1].trim();
                if next_t.is_empty() || next_t.starts_with("...") {
                    break;
                }
                idx += 1;
                parts.push(next_t);
                if next_t.ends_with("...") {
                    break;
                }
            }
            let stripped = unwrap_paragraphs(
                parts
                    .join(" ")
                    .trim_start_matches('.')
                    .trim_end_matches("...")
                    .trim_end_matches('.')
                    .trim(),
            );
            let is_callout = current.as_ref().is_some_and(|sec| {
                sec.body_lines.is_empty() && (sec.day_label.is_some() || stripped.len() > 64)
            });
            if is_callout {
                if let Some(sec) = current.as_mut() {
                    if sec.callout.is_none() {
                        sec.callout = Some(stripped.clone());
                    }
                    sec.body_lines.push(stripped);
                    sec.body_lines.push(String::new());
                }
            } else {
                if let Some(prev) = current.take() {
                    parsed_sections.push(prev);
                } else if !general_lines.is_empty() {
                    let body = unwrap_paragraphs(&general_lines.join("\n"));
                    if !body.is_empty() {
                        parsed_sections.push(QpfhsdSection {
                            heading: "Overview".into(),
                            days: [true, true, true],
                            day_label: None,
                            callout: None,
                            body_lines: vec![body],
                        });
                    }
                    general_lines.clear();
                }
                current = Some(QpfhsdSection {
                    heading: stripped,
                    days: [true, true, true],
                    day_label: None,
                    callout: None,
                    body_lines: Vec::new(),
                });
            }
        } else if let Some(sec) = current.as_mut() {
            if sec.body_lines.is_empty() {
                if trimmed.is_empty() {
                    idx += 1;
                    continue;
                }
                if sec.day_label.is_none() {
                    if let Some((days, label)) = parse_qpfhsd_day_tag(trimmed) {
                        sec.days = days;
                        sec.day_label = Some(label);
                        idx += 1;
                        continue;
                    }
                }
            }
            sec.body_lines.push(lines[idx].to_owned());
        } else {
            general_lines.push(lines[idx]);
        }
        idx += 1;
    }
    if let Some(prev) = current.take() {
        parsed_sections.push(prev);
    } else if !general_lines.is_empty() {
        let body = unwrap_paragraphs(&general_lines.join("\n"));
        if !body.is_empty() {
            parsed_sections.push(QpfhsdSection {
                heading: "Discussion".into(),
                days: [true, true, true],
                day_label: None,
                callout: None,
                body_lines: vec![body],
            });
        }
    }

    let day_idx = day.and_then(|d| (1..=3).contains(&d).then_some((d - 1) as usize));
    let selected: Vec<&QpfhsdSection> = match day_idx {
        Some(di) => {
            let matching: Vec<&QpfhsdSection> =
                parsed_sections.iter().filter(|sec| sec.days[di]).collect();
            if matching.is_empty() {
                parsed_sections.iter().collect()
            } else {
                matching
            }
        }
        None => parsed_sections.iter().collect(),
    };

    let mut sections: Vec<DiscussionSection> = Vec::new();
    let mut headline = String::new();
    let mut region_names: Vec<String> = Vec::new();
    for sec in selected {
        let heading = match &sec.day_label {
            Some(label) => format!("{} ({label})", sec.heading),
            None => sec.heading.clone(),
        };
        if headline.is_empty() {
            if let Some(callout) = &sec.callout {
                headline = callout.clone();
            }
        }
        region_names.push(sec.heading.clone());
        push_section(&mut sections, heading, &sec.body_lines.join("\n"));
    }
    if sections.is_empty() {
        return None;
    }
    if headline.is_empty() && !region_names.is_empty() {
        headline = format!(
            "Heavy snow and icing discussion: {}",
            region_names.join("; ")
        );
    }
    let summary = sections
        .first()
        .map(|sec| extract_summary_sentences(&sec.body, MAX_SUMMARY_CHARS))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| headline.clone());
    Some((
        valid_from,
        valid_to,
        OutlookDiscussion {
            headline: cap_text(&headline, MAX_HEADLINE_CHARS),
            summary: cap_text(&summary, 1_450),
            sections,
            issued: issued.map(format_rfc3339_utc),
            source_url: source_url.to_owned(),
        },
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_spc_day1_summary_headline_and_regional_sections() {
        let sample = "\
ZCZC SPCSWODY1 ALL
ACUS01 KWNS 250559
SPC AC 250559

Day 1 Convective Outlook\x20\x20
NWS Storm Prediction Center Norman OK
1159 PM CST Tue Nov 24 2026

Valid 251200Z - 261200Z

...THERE IS A SLIGHT RISK OF SEVERE THUNDERSTORMS ACROSS THE FLORIDA
KEYS AND FAR SOUTHERN FLORIDA PENINSULA...

...SUMMARY...
Isolated severe storms capable of damaging winds and tornadoes or
waterspouts appear possible over the Florida Keys and southern
Florida peninsula today.

...Synopsis...
Late-evening water-vapor imagery reveals an upper low over the
southern Plains drifting east.

...Florida Keys/southern FL peninsula...
As of 05:30 UTC, imagery already shows mid-level rotation.

..Moore/Dean.. 11/25/2026

$$";
        let got = parse_spc_discussion(sample, 1, None).unwrap();
        assert_eq!(
            got.headline,
            "THERE IS A SLIGHT RISK OF SEVERE THUNDERSTORMS ACROSS THE FLORIDA KEYS AND FAR SOUTHERN FLORIDA PENINSULA"
        );
        assert_eq!(
            got.summary,
            "Isolated severe storms capable of damaging winds and tornadoes or waterspouts appear possible over the Florida Keys and southern Florida peninsula today."
        );
        assert_eq!(got.issued.as_deref(), Some("2026-11-25T05:59:00Z"));
        assert_eq!(got.sections.len(), 2);
        assert_eq!(got.sections[0].heading, "Synopsis");
        assert_eq!(
            got.sections[1].heading,
            "Florida Keys/southern FL peninsula"
        );
        assert_eq!(
            got.source_url,
            "https://www.spc.noaa.gov/products/outlook/day1otlk.html"
        );
    }

    #[test]
    fn parses_spc_quiet_day_without_trailing_signature() {
        let sample = "\
Day 3 Convective Outlook\x20\x20
NWS Storm Prediction Center Norman OK
0105 AM CST Wed Nov 25 2026

Valid 271200Z - 281200Z

...NO THUNDERSTORM AREAS FORECAST...

...SUMMARY...
Thunderstorms are unlikely on Friday.

...Synopsis...
High pressure will reinforce stable conditions across much of the U.S. through late afternoon.

..Jewell.. 11/25/2026

$$";
        let got = parse_spc_discussion(sample, 3, None).unwrap();
        assert_eq!(got.headline, "NO THUNDERSTORM AREAS FORECAST");
        assert_eq!(got.summary, "Thunderstorms are unlikely on Friday.");
        assert_eq!(got.issued.as_deref(), Some("2026-11-25T07:05:00Z"));
        assert_eq!(got.sections.len(), 1);
    }

    #[test]
    fn parses_wpc_qpferd_all_three_days_and_quiet_day3() {
        let sample = "\
FOUS30 KWBC 250736
QPFERD

Excessive Rainfall Discussion
NWS Weather Prediction Center College Park MD
236 AM EST Wed Nov 25 2026

Day 1
Valid 12Z Wed Nov 25 2026 - 12Z Thu Nov 26 2026

...THERE IS A SLIGHT RISK OF EXCESSIVE RAINFALL ACROSS PORTIONS OF
THE COASTAL CAROLINAS AND SOUTH FLORIDA...

...Southeast U.S. Coast...

A strong surface low is organizing off the Florida coast. Rates of
1-1.5 inches per hour may reach 3.5 inches near Morehead City.

Day 2
Valid 12Z Thu Nov 26 2026 - 12Z Fri Nov 27 2026

...THERE IS A SLIGHT RISK OF EXCESSIVE RAINFALL FROM THE DELMARVA
PENINSULA TO THE GREATER NEW YORK CITY AREA.

The powerful surface low tracking along the East Coast will make
weather headlines for the holiday. Rainfall rates near 1 inch per hour
are likely along the corridor. Another sentence follows later.

Day 3
Valid 12Z Fri Nov 27 2026 - 12Z Sat Nov 28 2026

The probability of rainfall exceeding flash flood guidance is less
than 5 percent.

It is important to note that southwestern Washington is close to
needing a Marginal Risk for Day 3. Higher IVT values could warrant an
upgrade.

Hamrick

Day 4 and Day 5
Valid 12Z Sat Nov 28 2026 - 12Z Mon Nov 30 2026

Day 1 threat area: www.wpc.ncep.noaa.gov/qpf/94epoints.txt";

        let days = parse_wpc_qpferd(sample);
        assert_eq!(days.len(), 3);

        let d1 = &days[&1];
        assert_eq!(
            d1.valid_from.map(format_rfc3339_utc).as_deref(),
            Some("2026-11-25T12:00:00Z")
        );
        assert_eq!(
            d1.valid_to.map(format_rfc3339_utc).as_deref(),
            Some("2026-11-26T12:00:00Z")
        );
        assert_eq!(
            d1.discussion.issued.as_deref(),
            Some("2026-11-25T07:36:00Z")
        );
        assert_eq!(d1.discussion.sections[0].heading, "Southeast U.S. Coast");
        assert!(d1.discussion.summary.contains("3.5 inches"));

        let d2 = &days[&2];
        assert_eq!(
            d2.discussion.headline,
            "THERE IS A SLIGHT RISK OF EXCESSIVE RAINFALL FROM THE DELMARVA PENINSULA TO THE GREATER NEW YORK CITY AREA"
        );
        assert_eq!(d2.discussion.sections.len(), 1);
        assert_eq!(d2.discussion.sections[0].heading, "Discussion");
        assert_eq!(
            d2.discussion.summary,
            "The powerful surface low tracking along the East Coast will make weather headlines for the holiday. Rainfall rates near 1 inch per hour are likely along the corridor."
        );

        let d3 = &days[&3];
        assert!(d3.discussion.headline.contains("less than 5 percent"));
        assert!(!d3.discussion.sections[0].body.contains("Hamrick"));
        assert!(d3.discussion.summary.contains("southwestern Washington"));
    }

    #[test]
    fn parses_wpc_prev_day1_html_and_strips_links() {
        let html = r#"<html><body><pre>
Excessive Rainfall Discussion
NWS Weather Prediction Center College Park MD
754 PM EST Tue Nov 24 2026
Day 1
Valid 01Z Wed Nov 25 2026 - 12Z Wed Nov 25 2026

...THERE IS A MARGINAL RISK OF EXCESSIVE RAINFALL ACROSS PORTIONS
OF FLORIDA AND CALIFORNIA...

...Northern California...

Changes made earlier today remain valid.

Day 1 threat area: <a href="https://www.wpc.ncep.noaa.gov/qpf/94epoints.txt"<u>www.wpc.ncep.noaa.gov/qpf/94epoints.txt</u></a>
</pre></body></html>"#;
        let url = "https://www.wpc.ncep.noaa.gov/qpf/ero.php?opt=prev&day=1";
        let got = parse_wpc_ero_page(html, 1, url).unwrap();
        assert_eq!(
            got.valid_from.map(format_rfc3339_utc).as_deref(),
            Some("2026-11-25T01:00:00Z")
        );
        assert_eq!(got.discussion.source_url, url);
        assert_eq!(got.discussion.sections.len(), 1);
        assert_eq!(
            got.discussion.summary,
            "Changes made earlier today remain valid."
        );
    }

    #[test]
    fn parses_wpc_qpfhsd_with_day_filtering_callout_and_html() {
        let sample = r#"<pre><font face="arial,helvetica">
FOUS11 KWBC 250834
QPFHSD

Probabilistic Heavy Snow and Icing Discussion
NWS Weather Prediction Center College Park MD
334 AM EST Wed Nov 25 2026

Valid 12Z Wed Nov 25 2026 - 12Z Sat Nov 28 2026


...Middle Mississippi Valley...

Days 1-2...

...Significant snowfall likely to produce hazardous travel impacts
in eastern Missouri and western Illinois tonight and into
Thanksgiving Day...

Over the last 24 hours, guidance has converged on heavy snow.
Rates may reach 1 to 2 inches per hour tonight.

...Central Appalachians...

Day 3...

Upslope snow develops behind the departing low on Friday.
Light accumulations remain possible along higher ridges.

Fracasso/Asherman/Mullinax

$$
</font></pre>"#;
        let url = "https://www.wpc.ncep.noaa.gov/wwd/wssi/wssi.php";
        let (vf, vt, d1) = parse_wpc_qpfhsd(sample, Some(1), url).unwrap();
        assert_eq!(
            vf.map(format_rfc3339_utc).as_deref(),
            Some("2026-11-25T12:00:00Z")
        );
        assert_eq!(
            vt.map(format_rfc3339_utc).as_deref(),
            Some("2026-11-28T12:00:00Z")
        );
        assert_eq!(d1.issued.as_deref(), Some("2026-11-25T08:34:00Z"));
        assert_eq!(d1.sections.len(), 1);
        assert_eq!(
            d1.sections[0].heading,
            "Middle Mississippi Valley (Days 1–2)"
        );
        assert!(d1.headline.contains("Significant snowfall likely"));
        assert!(!d1.sections[0].body.contains("Fracasso"));

        let (_, _, d3) = parse_wpc_qpfhsd(sample, Some(3), url).unwrap();
        assert_eq!(d3.sections.len(), 1);
        assert_eq!(d3.sections[0].heading, "Central Appalachians (Day 3)");
        assert!(d3.headline.contains("Central Appalachians"));
        assert!(d3.summary.contains("Upslope snow develops"));
    }
}
