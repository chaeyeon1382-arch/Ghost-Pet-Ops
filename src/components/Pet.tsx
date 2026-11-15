import type { PetState } from "../domain/types";

const ART: Record<PetState, string> = {
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
 |   /‾\   |
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

const CAPTION: Record<PetState, string> = {
  healthy: "Boo is happy and floating.",
  slow: "Boo is sluggish. Responses are slow.",
  down: "Boo has faded away. The service is down.",
  recovering: "Boo is coming back...",
};

export function Pet({ state }: { state: PetState }) {
  return (
    <figure className={`pet pet--${state}`}>
      <pre aria-hidden="true">{ART[state]}</pre>
      <figcaption>{CAPTION[state]}</figcaption>
    </figure>
  );
}
