"""Tracked NFT collections: (chain, address, expected name).

The list is the well-known blue-chip / high-volume collections. Every address is verified against
Alchemy's `getContractMetadata` before ingestion (`python -m ingest verify`): a name mismatch or a
non-NFT contract is skipped and logged, never ingested silently. Per-collection cap: 6,000 tokens
(Otherdeed / Art Blocks have 100k+; the cap keeps the index diverse; 48 collections -> ~270k tokens).

Base mainnet needs the network enabled on the Alchemy app (dashboard -> app -> networks); its
collections are ingested by the same code once `--chain base` works.
"""

CAP = 6_000

ETHEREUM = [
    ("0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D", "BoredApeYachtClub"),
    ("0x60E4d786628Fea6478F785A6d7e704777c86a7c6", "MutantApeYachtClub"),
    ("0xED5AF388653567Af2F388E6224dC7C4b3241C544", "Azuki"),
    ("0xBd3531dA5CF5857e7CfAA92426877b022e612cf8", "PudgyPenguins"),
    ("0x8a90CAb2b38dba80c64b7734e58Ee1dB38B8992e", "Doodles"),
    ("0x49cF6f5d44E70224e2E23fDcdd2C053F30aDA28B", "CloneX"),
    ("0x23581767a106ae21c074b2276D25e5C3e136a68b", "Moonbirds"),
    ("0x7Bd29408f11D2bFC23c34f18275bBf23bB716Bc7", "Meebits"),
    ("0xe785E82358879F061BC3dcAC6f0444462D4b5330", "World Of Women"),
    ("0x1A92f7381B9F03921564a437210bB9396471050C", "Cool Cats"),
    ("0x34d85c9CDeB23FA97cb08333b511ac86E1C4E258", "Otherdeed"),
    ("0x524cAB2ec69124574082676e6F654a18df49A048", "LilPudgys"),
    ("0xB6a37b5d14D502c3Ab0Ae6f3a0E058BC9517786e", "Elementals"),
    ("0x5Af0D9827E0c53E4799BB226655A1de152A425a5", "Milady"),
    ("0x8821BeE2ba0dF28761AffF119D66390D594CD280", "DeGods"),
    ("0xba30E5F9Bb24caa003E9f2f0497Ad287FDF95623", "BoredApeKennelClub"),
    ("0x1CB1A5e65610AEFF2551A50f76a87a7d3fB649C6", "CrypToadz"),
    ("0x79FCDEF22feeD20eDDacbB2587640e45491b757f", "mfer"),
    ("0x59468516a8259058baD1cA5F8f4BFF190d30E066", "Invisible Friends"),
    ("0x1792a96E5668ad7C167ab804a100ce42395Ce54D", "Moonbirds Oddities"),
    ("0xa7d8d9ef8D8Ce8992Df33D8b8CF4Aebabd5bD270", "Art Blocks"),
    ("0x059EDD72Cd353dF5106D2B9cC5ab83a52287aC3a", "Art Blocks"),
    ("0xa3AEe8BcE55BEeA1951EF834b99f3Ac60d1ABeeB", "VeeFriends"),
    ("0x9C8fF314C9Bc7F6e59A9d9225Fb22946427eDC03", "Nouns"),
    ("0x364C828eE171616a39897688A831c2499aD972ec", "Sappy Seals"),
    ("0x036721e5A769Cc48B3189EfbB9ccE4471E8A48B1", "Checks"),
    ("0x6339e5E072086621540D0362C4e3Cea0d643E114", "Opepen"),
    ("0x8943C7bAC1914C9A7ABa750Bf2B6B09Fd21037E0", "Lazy Lions"),
    ("0x3bf2922f4520a8BA0c2eFC3D2a1539678DaD5e9D", "0N1 Force"),
    ("0xC2C747E0F7004F9E8817Db2ca4997657a7746928", "Hashmasks"),
    ("0x2acAb3DEa77832C09420663b0E1cB386031bA17B", "Deadfellaz"),
    ("0xaCF63E56fd08970b43401492a02F6F38B6635C91", "Kanpai Pandas"),
    ("0xbCe3781ae7Ca1a5e050Bd9C4c77369867eBc307e", "goblintown"),
    ("0x769272677faB02575E84945F03Eca517ACc544Cc", "Captainz"),
    ("0x39ee2c7b3cb80254225884ca001F57118C8f21B6", "Potatoz"),
    ("0xb7F7F6C52F2e2fdb1963Eab30438024864c313F6", "Wrapped Cryptopunks"),
    ("0xB852c6b5892256C264Cc2C888eA462189154D8d7", "rektguy"),
    ("0x32973908FaeE0Bf825A343000fE412ebE56F802A", "Pixelmon"),
    ("0x7D8820FA92EB1584636f4F5b8515B5476B75171a", "Murakami.Flowers"),
    ("0x306b1ea3ecdf94aB739F1910bbda052Ed4A9f949", "Beanz"),
    ("0x231d3559aa848Bf10366fB9868590F01d34bF240", "Valhalla"),
    ("0x892848074ddeA461A15f337250Da3ce55580CA85", "CyberBrokers"),
    ("0x6632a9d63E142F17a668064D41A21193b49B41a0", "Prime Ape Planet"),
    ("0x4b10701Bfd7BFEdc47d50562b76b436fbB5BdB3B", "Lil Nouns"),
    ("0x80336Ad7A747236ef41F47ed2C7641828a480BAA", "Chimpers"),
    ("0x3903d4fFaAa700b62578a66e7a67Ba4cb67787f9", "Quirkies"),
    ("0x0c2E57EFddbA8c768147D1fdF9176a0A6EBd5d83", "Kaiju Kingz"),
    ("0xEdB61f74B0d09B2558F1eeb79B247c1F363Ae452", "Gutter Cat Gang"),
    ("0xc92cedDfb8dd984A89fb494c376f9A48b999aAFc", "Creature World"),
]

BASE: list[tuple[str, str]] = []  # filled once Base mainnet is enabled on the Alchemy app (see docstring)

CHAINS = {
    "eth": {"chain_id": 1, "alchemy": "eth-mainnet", "collections": ETHEREUM},
    "base": {"chain_id": 8453, "alchemy": "base-mainnet", "collections": BASE},
}
