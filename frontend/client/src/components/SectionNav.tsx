import { useEffect, useState } from "react";

export type NavItem = { id: string; label: string };

/** Sticky jump links so long result pages don't need endless scrolling. */
export function SectionNav({ items }: { items: NavItem[] }) {
  const [active, setActive] = useState(items[0]?.id ?? "");

  useEffect(() => {
    const targets = items.map((item) => document.getElementById(item.id)).filter((el): el is HTMLElement => el !== null);
    if (!targets.length || typeof IntersectionObserver === "undefined") return;
    const inBand = new Set<Element>();
    const observer = new IntersectionObserver(
      (entries) => {
        // Callbacks only report changed entries, so keep the full set of sections in the reading band.
        entries.forEach((entry) => (entry.isIntersecting ? inBand.add(entry.target) : inBand.delete(entry.target)));
        const topmost = Array.from(inBand).sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0];
        if (topmost) setActive(topmost.id);
      },
      { rootMargin: "-15% 0px -70% 0px" },
    );
    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, [items]);

  const jump = (id: string) => {
    setActive(id);
    document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  };

  return (
    <nav className="section-nav" aria-label="Jump to section">
      {items.map((item) => (
        <button key={item.id} type="button" className={`section-chip ${active === item.id ? "is-active" : ""}`} aria-current={active === item.id ? "true" : undefined} onClick={() => jump(item.id)}>{item.label}</button>
      ))}
    </nav>
  );
}
