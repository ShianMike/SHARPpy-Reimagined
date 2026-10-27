import { GripHorizontal, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { displayUnit } from "../../lib/displayUnits";
import type { FieldProduct } from "../../types/map";
import { Action, IconAction } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import styles from "./ForecastControls.module.css";

export function ParameterPicker({ products, selected, anchor, onClose }: {
  products: readonly FieldProduct[]; selected: string | null; anchor: HTMLElement | null; onClose: () => void;
}): ReactNode {
  const units = useUiStore((state) => state.units);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [preview, setPreview] = useState(selected);
  const groups = [...new Set(products.map((product) => product.category))];
  const categories = ["All", ...groups.filter((item) => item !== "Winter Weather"), ...groups.filter((item) => item === "Winter Weather")];
  const matches = products.filter((product) => `${product.label} ${product.shortLabel} ${product.description}`.toLowerCase().includes(query.trim().toLowerCase()));
  const results = matches.filter((product) => category === "All" || product.category === category);
  const detail = results.find((product) => product.key === preview) ?? results.find((product) => product.key === selected) ?? results[0];
  const done = (): void => { onClose(); anchor?.focus(); };
  return <>
    <Surface as="section" elevation="popover" aria-label="Parameter browser" className={styles.parameterMain}>
    <header className={styles.dialogHeader} data-popover-drag><IconAction icon={GripHorizontal} size="sm" label="Move popup" data-popover-move aria-description="Drag to move. Arrow keys move the popup; Home centers it on the screen." /><h2>Parameters</h2><IconAction icon={X} size="sm" label="Close parameter selector" onClick={done} /></header>
    <label className={styles.search}><input data-autofocus type="search" aria-label="Search parameters" placeholder="Find a parameter…" value={query} onChange={(event) => { setQuery(event.target.value); }} /></label>
    <div className={styles.parameterBrowser}>
      <nav aria-label="Parameter categories" className={styles.categories}>{categories.map((item) => <button type="button" key={item} aria-current={category === item ? "true" : undefined} onClick={() => { setCategory(item); }}><span>{item}</span><small>{item === "All" ? matches.length : matches.filter((product) => product.category === item).length}</small></button>)}</nav>
      <section key={category} data-motion="fade" className={styles.results} aria-label="Parameter results">
        <div className={styles.resultHeading}><strong>{category === "All" ? "All parameters" : category}</strong><span aria-live="polite">{results.length}</span></div>
        {results.map((product) => <button type="button" className={styles.product} key={product.key} aria-label={`${product.label}, ${displayUnit(product.palette.units, units, product.palette.display) || "Index"}`} aria-pressed={selected === product.key} aria-description={`${product.description} ${product.caveat}`} onFocus={() => { setPreview(product.key); }} onMouseEnter={() => { setPreview(product.key); }} onClick={() => {
          useMapStore.getState().setProduct(product.key); useMapStore.getState().setLayerVisible("model-field", true); done();
        }}><strong>{product.label}</strong><span className={styles.productUnit}>{displayUnit(product.palette.units, units, product.palette.display) || "Index"}</span></button>)}
        {results.length > 0 ? null : <div className={styles.empty}><p>No matching parameters.</p><Action onClick={() => { setQuery(""); setCategory("All"); }}>Reset filters</Action></div>}
      </section>
    </div>
    </Surface>
    <Surface as="section" elevation="popover" aria-label="Parameter description" className={styles.parameterDetail} id="parameter-description" tabIndex={0}>{detail === undefined ? <p>Search by name or choose a category.</p> : <><strong>{detail.label}<span className={styles.productUnit}>{displayUnit(detail.palette.units, units, detail.palette.display) || "Index"}</span></strong><p>{detail.description}</p>{detail.caveat === "" ? null : <p>{detail.caveat}</p>}</>}</Surface>
  </>;
}
