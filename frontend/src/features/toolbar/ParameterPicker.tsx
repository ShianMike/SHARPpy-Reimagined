import { Check, GripHorizontal, Search, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useMapStore } from "../../stores/mapStore";
import type { FieldProduct } from "../../types/map";
import { Action, IconAction } from "../../ui/Controls";
import styles from "./ForecastControls.module.css";

export function ParameterPicker({ products, selected, anchor, onClose }: {
  products: readonly FieldProduct[]; selected: string | null; anchor: HTMLElement | null; onClose: () => void;
}): ReactNode {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [preview, setPreview] = useState(selected);
  const categories = ["All", ...new Set(products.map((product) => product.category))];
  const matches = products.filter((product) => `${product.label} ${product.shortLabel} ${product.description}`.toLowerCase().includes(query.trim().toLowerCase()));
  const results = matches.filter((product) => category === "All" || product.category === category);
  const detail = results.find((product) => product.key === preview) ?? results.find((product) => product.key === selected) ?? results[0];
  const done = (): void => { onClose(); anchor?.focus(); };
  return <>
    <header className={styles.dialogHeader} data-popover-drag><IconAction icon={GripHorizontal} size="sm" label="Move popup" data-popover-move aria-description="Drag to move. Arrow keys move the popup; Home returns it to the toolbar." /><h2>Parameters</h2><IconAction icon={X} size="sm" label="Close parameter selector" onClick={done} /></header>
    <label className={styles.search}><Search className="icon-sm" aria-hidden="true" /><input data-autofocus type="search" aria-label="Search parameters" placeholder="Find a parameter…" value={query} onChange={(event) => { setQuery(event.target.value); }} /></label>
    <div className={styles.parameterBrowser}>
      <nav aria-label="Parameter categories" className={styles.categories}>{categories.map((item) => <button type="button" key={item} aria-current={category === item ? "true" : undefined} onClick={() => { setCategory(item); }}><span>{item}</span><small>{item === "All" ? matches.length : matches.filter((product) => product.category === item).length}</small></button>)}</nav>
      <section key={category} data-motion="fade" className={styles.results} aria-label="Parameter results">
        <div className={styles.resultHeading}><strong>{category === "All" ? "All parameters" : category}</strong><span aria-live="polite">{results.length}</span></div>
        {results.map((product) => <button type="button" className={styles.product} key={product.key} aria-label={`${product.label}, ${product.palette.units || "Index"}`} aria-pressed={selected === product.key} aria-description={`${product.description} ${product.caveat}`} onFocus={() => { setPreview(product.key); }} onMouseEnter={() => { setPreview(product.key); }} onClick={() => {
          useMapStore.getState().setProduct(product.key); useMapStore.getState().setLayerVisible("model-field", true); done();
        }}><strong>{product.label}</strong><span className={styles.productUnit}>{product.palette.units || "Index"}</span><span className={styles.check}>{selected === product.key ? <Check className="icon-sm" aria-hidden="true" /> : null}</span></button>)}
        {results.length > 0 ? null : <div className={styles.empty}><p>No matching parameters.</p><Action onClick={() => { setQuery(""); setCategory("All"); }}>Reset filters</Action></div>}
      </section>
    </div>
    <footer className={styles.parameterDetail} id="parameter-description">{detail === undefined ? <p>Search by name or choose a category.</p> : <><strong>{detail.shortLabel}<span>{detail.palette.units || "Index"}</span></strong><p>{detail.description}</p>{detail.caveat === "" ? null : <p>{detail.caveat}</p>}</>}</footer>
  </>;
}
