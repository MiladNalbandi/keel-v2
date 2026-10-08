// keel Product's web pages (an add-on: keel's web finds this file when it is built, and loads it only when Product is on).

import type { AddonWeb } from "../../web/src/addons";
import Initiatives from "./Initiatives";
import Teams from "./Teams";
import "./product.css";

const product: AddonWeb = { name: "product", pages: { initiatives: Initiatives, teams: Teams } };

export default product;
