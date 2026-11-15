import type { Status } from "../domain/types";

const ART: Record<Status, string> = {
  unknown: String.raw`
   .-----.
  /  . .  \
 |         |  ?
 |   ...   |
 |         |
 '~^~^~^~^~'`,
  healthy: String.raw`
   .-----.
  /  ^ ^  \
 |    o    |
 |  \___/  |
 |         |
 '~^~^~^~^~'`,
  slow: String.raw`
   .-----.
  /  - -  \
 |    o    |  z
 |   ___   | z
 |         |
 '~^~^~^~^~'`,
  down: String.raw`
   .-----.
  /  x x  \
 |    o    |
 |   /^\   |
 |         |
 '~ ~ ~ ~ ~'`,
  recovering: String.raw`
   .-----.
  /  o o  \
 |    o    |  +
 |   ___   |
 |         |
 '~^~ ~^~ ~'`,
};

const CAPTION: Record<Status, string> = {
  unknown: "Waiting for the first health check...",
  healthy: "Boo is happy and floating.",
  slow: "Boo is sluggish. Responses are slow.",
  down: "Boo has faded away. The service is down.",
  recovering: "Boo is coming back...",
};

export function Pet({ status }: { status: Status }) {
  return (
    <figure className={`pet pet--${status}`}>
      <pre aria-hidden="true">{ART[status]}</pre>
      <figcaption>{CAPTION[status]}</figcaption>
    </figure>
  );
}
