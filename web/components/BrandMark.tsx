type BrandMarkProps = {
  size?: number;
  className?: string;
  title?: string;
};

export default function BrandMark({ size = 32, className = "", title = "SceneWorks" }: BrandMarkProps) {
  return (
    <svg
      aria-label={title}
      role="img"
      width={size}
      height={size}
      viewBox="0 0 40 40"
      className={`brand-mark ${className}`.trim()}
    >
      <rect className="brand-mark-bg" x="2" y="2" width="36" height="36" rx="10" />
      <path
        className="brand-mark-layer brand-mark-layer-top"
        d="M9 13.2 20 7.7l11 5.5-11 5.5L9 13.2Z"
      />
      <path
        className="brand-mark-layer brand-mark-layer-mid"
        d="m9 19.8 11 5.5 11-5.5"
      />
      <path
        className="brand-mark-layer brand-mark-layer-bottom"
        d="m9 26.4 11 5.5 11-5.5"
      />
    </svg>
  );
}
