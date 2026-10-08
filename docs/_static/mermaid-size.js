// Mermaid-Diagramme höchstens in ihrer natürlichen Breite zeigen: sphinxcontrib-mermaid
// zieht sie sonst auf die volle Spaltenbreite, kleine Diagramme werden riesig.
document.addEventListener("DOMContentLoaded", () => {
  const fit = () => {
    document.querySelectorAll("pre.mermaid > svg[viewBox]").forEach((svg) => {
      const width = svg.viewBox.baseVal.width;
      if (width) svg.style.setProperty("max-width", `${Math.ceil(width)}px`, "important");
    });
  };
  new MutationObserver(fit).observe(document.body, { subtree: true, childList: true });
});
