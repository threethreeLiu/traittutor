import type { SelectedBookReference } from "@/lib/book-references";

/** Historical references remain renderable, but new Book Engine selection is removed. */
export default function BookReferencePicker(_props: {
  open: boolean;
  initialReferences: SelectedBookReference[];
  onClose: () => void;
  onApply: (references: SelectedBookReference[]) => void;
}) {
  return null;
}
