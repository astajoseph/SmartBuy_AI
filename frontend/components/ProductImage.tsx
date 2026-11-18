/* eslint-disable @next/next/no-img-element -- images come from many store CDNs; next/image would need each host allow-listed */
export default function ProductImage({ src, name, className = "" }: { src: string | null | undefined; name: string; className?: string }) {
  return (
    <div className={`flex items-center justify-center overflow-hidden bg-white ${className}`}>
      {src
        ? <img src={src} alt={name} className="max-h-full max-w-full object-contain" loading="lazy" />
        : <span className="text-3xl font-bold text-slate-300" aria-label={name}>{name.slice(0, 1)}</span>}
    </div>
  );
}
