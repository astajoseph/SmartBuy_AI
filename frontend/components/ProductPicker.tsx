"use client";
import type { ProductSummary } from "@/types/api";

export default function ProductPicker({ products, value, onChange }: { products: ProductSummary[] | null; value: string; onChange: (id: string) => void }) {
  return (
    <div>
      <label className="label" htmlFor="product">Product</label>
      <select id="product" className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Select a product…</option>
        {products?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
    </div>
  );
}
