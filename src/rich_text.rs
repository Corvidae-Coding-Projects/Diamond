//! Bounded Markdown rendering into Diamond's trusted `Html` value.

use crate::error::{Error, Result};
use pulldown_cmark::{Event, Options, Parser, Tag, TagEnd, html};

const MAX_MARKDOWN_BYTES: usize = 256 * 1024;
const MAX_RENDERED_BYTES: usize = 1024 * 1024;

/// Render untrusted Markdown without admitting raw HTML, embedded images, or
/// active URL schemes. The Markdown parser owns escaping for emitted text and
/// attributes; application code cannot construct `Html` from arbitrary text.
pub(crate) fn render(markdown: &str) -> Result<String> {
    if markdown.len() > MAX_MARKDOWN_BYTES {
        return Err(Error::Limit("Markdown input"));
    }

    let options = Options::ENABLE_STRIKETHROUGH
        | Options::ENABLE_TABLES
        | Options::ENABLE_TASKLISTS
        | Options::ENABLE_FOOTNOTES;
    let events = Parser::new_ext(markdown, options).filter_map(safe_event);
    let mut output = String::new();
    html::push_html(&mut output, events);
    if output.len() > MAX_RENDERED_BYTES {
        return Err(Error::Limit("rendered Markdown"));
    }
    Ok(output)
}

fn safe_event(event: Event<'_>) -> Option<Event<'_>> {
    match event {
        Event::Html(_)
        | Event::InlineHtml(_)
        | Event::Start(Tag::Image { .. })
        | Event::End(TagEnd::Image) => None,
        Event::Start(Tag::Link {
            link_type,
            dest_url,
            title,
            id,
        }) => Some(Event::Start(Tag::Link {
            link_type,
            dest_url: if safe_link(&dest_url) {
                dest_url
            } else {
                "#".into()
            },
            title,
            id,
        })),
        other => Some(other),
    }
}

fn safe_link(destination: &str) -> bool {
    if destination.chars().any(char::is_control) {
        return false;
    }
    if destination.starts_with('#') {
        return true;
    }
    if destination.starts_with('/') {
        return !destination.starts_with("//");
    }
    if let Ok(url) = url::Url::parse(destination) {
        return matches!(url.scheme(), "http" | "https" | "mailto");
    }
    let first = destination
        .split(['/', '?', '#'])
        .next()
        .unwrap_or_default();
    !destination.starts_with('\\') && !first.contains([':', '&'])
}

#[cfg(test)]
mod tests {
    use super::render;

    #[test]
    fn removes_active_content_and_embedded_images() {
        let rendered = render(
            "# Safe\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(2)) ![leak](https://example.invalid/pixel) **ok**",
        )
        .unwrap_or_default();
        assert!(!rendered.contains("script"));
        assert!(!rendered.contains("javascript"));
        assert!(!rendered.contains("img"));
        assert!(rendered.contains("href=\"#\""));
        assert!(rendered.contains("<strong>ok</strong>"));
    }

    #[test]
    fn permits_bounded_navigation_links() {
        let rendered = render("[local](/docs) [web](https://example.invalid) [anchor](#part)")
            .unwrap_or_default();
        assert!(rendered.contains("href=\"/docs\""));
        assert!(rendered.contains("href=\"https://example.invalid\""));
        assert!(rendered.contains("href=\"#part\""));
    }

    #[test]
    fn neutralizes_ambiguous_and_active_schemes() {
        for destination in [
            "JAVASCRIPT:alert(1)",
            "data:text/html,attack",
            "vbscript:attack",
            "file:///etc/passwd",
            "//example.invalid/path",
            r"\\example.invalid\path",
            "java&#x73;cript&#x3a;alert(1)",
        ] {
            let rendered = render(&format!("[link]({destination})")).unwrap_or_default();
            assert!(
                !rendered.contains(destination) && rendered.contains("href=\"#\""),
                "destination was not neutralized: {destination:?} => {rendered:?}"
            );
        }
    }
}
