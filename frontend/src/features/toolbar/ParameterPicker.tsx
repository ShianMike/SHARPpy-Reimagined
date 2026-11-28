import { GripHorizontal, Search, X } from "lucide-react";
import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { displayUnit } from "../../lib/displayUnits";
import { groupMapParameters, normalizeParameterSearch } from "../../map/mapParameters";
import type { FieldProduct } from "../../types/map";
import { Action, IconAction } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import styles from "./ForecastControls.module.css";

export function ParameterPicker({ products, selected, anchor, onClose }: {
  products: readonly FieldProduct[]; selected: string | null; anchor: HTMLElement | null; onClose: () => void;
}): ReactNode {
  const units = useUiStore((state) => state.units);
  const [query, setQuery] = useState("");
  const [chosenCategory, setCategory] = useState("All");
  const [preview, setPreview] = useState(selected);
  const groups = useMemo(() => groupMapParameters(products), [products]);
  const matches = useMemo(() => {
    const filter = normalizeParameterSearch(query);
    return filter === "" ? groups : groups.map((group) => ({
      ...group,
      products: group.products.filter((product) =>
        normalizeParameterSearch(`${product.label} ${product.shortLabel} ${product.key} ${product.description}`).includes(filter)),
    }));
  }, [groups, query]);
  const categories = ["All", ...groups.map((group) => group.category)];
  const categoryIndex = Math.max(0, categories.indexOf(chosenCategory));
  const category = categories[categoryIndex] ?? "All";
  const resultGroups = matches.filter((group) => group.products.length > 0 && (category === "All" || group.category === category));
  const results = resultGroups.flatMap((group) => group.products);
  const matchesProduct = (product: FieldProduct, key: string | null): boolean =>
    key !== null && (product.key === key || product.variants?.some((variant) => variant.key === key) === true);
  const detail = results.find((product) => matchesProduct(product, preview))
    ?? results.find((product) => matchesProduct(product, selected))
    ?? results[0];
  const done = (): void => { onClose(); anchor?.focus(); };
  return <>
    <Surface as="section" elevation="popover" aria-label="Parameter browser" className={styles.parameterMain}>
    <header className={styles.dialogHeader} data-popover-drag><IconAction icon={GripHorizontal} size="sm" label="Move popup" data-popover-move aria-description="Drag to move. Arrow keys move the popup; Home centers it on the screen." /><h2>Parameters</h2><IconAction icon={X} size="sm" label="Close parameter selector" onClick={done} /></header>
    <label className={styles.search}><Search className="icon-sm" aria-hidden="true" /><input data-autofocus type="search" aria-label="Search parameters" placeholder="Find a parameter…" value={query} onChange={(event) => { setQuery(event.target.value); }} /></label>
    <div className={styles.parameterBrowser}>
      <nav aria-label="Parameter categories" className={styles.categories} data-selection-track="vertical"
        style={{ "--selection-index": categoryIndex } as CSSProperties}>
        <button type="button" aria-current={category === "All" ? "true" : undefined} onClick={() => { setCategory("All"); }}>
          <span>All</span><small>{matches.reduce((count, group) => count + group.products.length, 0)}</small>
        </button>
        {matches.map((group) => <button type="button" key={group.category} aria-current={category === group.category ? "true" : undefined}
          onClick={() => { setCategory(group.category); }}><span>{group.category}</span><small>{group.products.length}</small></button>)}
      </nav>
      <section key={category} data-motion="fade" className={styles.results} aria-label="Parameter results">
        <div className={styles.resultHeading}><strong>{category === "All" ? "All parameters" : category}</strong><span aria-live="polite">{results.length}</span></div>
        {resultGroups.map((group) => <div key={group.category} className={styles.parameterGroup}>
          {category === "All" ? <h3 className={styles.parameterGroupTitle}>{group.category}</h3> : null}
          {group.products.map((product) => {
            const unit = displayUnit(product.palette.units, units, product.palette.display) || "Index";
            return <button type="button" className={styles.product} key={product.key} aria-label={`${product.label}, ${unit}`}
              aria-pressed={matchesProduct(product, selected)} aria-description={`${product.description} ${product.caveat}`}
              onFocus={() => { setPreview(product.key); }} onMouseEnter={() => { setPreview(product.key); }} onClick={() => {
                const next = matchesProduct(product, selected) && selected !== null ? selected : product.key;
                useMapStore.getState().setProduct(next); useMapStore.getState().setLayerVisible("model-field", true); done();
              }}><strong>{product.label}</strong><span className={styles.productUnit}>{unit}</span></button>;
          })}
        </div>)}
        {results.length > 0 ? null : <div className={styles.empty}><p>No matching parameters.</p><Action onClick={() => { setQuery(""); setCategory("All"); }}>Reset filters</Action></div>}
      </section>
    </div>
    </Surface>
    <Surface as="section" elevation="popover" aria-label="Parameter description" className={styles.parameterDetail} id="parameter-description" tabIndex={0}>{detail === undefined ? <p>Search by name or choose a category.</p> : <><strong>{detail.label}<span className={styles.productUnit}>{displayUnit(detail.palette.units, units, detail.palette.display) || "Index"}</span></strong><p>{detail.description}</p>{detail.caveat === "" ? null : <p>{detail.caveat}</p>}</>}</Surface>
  </>;
}
