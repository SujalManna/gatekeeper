// JSX typings for the Material Web custom elements used in this app.
import type { DetailedHTMLProps, HTMLAttributes, Ref } from "react";

type MdProps = DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
  ref?: Ref<HTMLElement>;
  [attr: string]: unknown;
};

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "md-filled-button": MdProps;
      "md-filled-tonal-button": MdProps;
      "md-outlined-button": MdProps;
      "md-text-button": MdProps;
      "md-assist-chip": MdProps;
      "md-chip-set": MdProps;
      "md-dialog": MdProps;
      "md-elevation": MdProps;
      "md-icon": MdProps;
      "md-circular-progress": MdProps;
      "md-linear-progress": MdProps;
      "md-ripple": MdProps;
      "md-switch": MdProps;
      "md-primary-tab": MdProps;
      "md-tabs": MdProps;
      "md-outlined-text-field": MdProps;
    }
  }
}
