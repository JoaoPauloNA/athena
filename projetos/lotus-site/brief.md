# Brief — Modernização Lótus Soluções Contábeis

> Orquestrador: Athena-MCP · Cliente: projeto real de modernização
> Origem do conteúdo: https://www.lotus.cnt.br/ (site atual, template 2022)

## Objetivo

Criar protótipo moderno da landing page institucional (estático, HTML+CSS+JS
puro, sem build) que possa substituir o site atual. Foco: visual 2026,
responsivo, rápido, mantendo TODO o conteúdo real do negócio.

## Identidade

- Nome: **Lótus Soluções Contábeis** — contabilidade em Cuiabá/MT
- Tom: profissional, confiável, moderno — público: empresários (agronegócio,
  serviços, comércio, digital)
- Sugestão de paleta: verde-escuro/dourado (remete a lótus e prosperidade),
  fundo claro, tipografia sans moderna (system stack ou Google Fonts via CDN)

## Conteúdo real (usar, não inventar)

**Serviços (5):** Departamento Pessoal · Departamento Fiscal · Contabilidade
Societária · Auditoria Contábil · BPO — Terceirização do Financeiro

**Segmentos (6):** Agronegócio · Profissionais Liberais · Prestação de
Serviços · Comércio · Associações de Classes · OSCIPs

**Especialidades digitais:** Infoprodutores · Gestores de Tráfego · Agências
de Lançamento · E-commerce · Youtubers

**Equipe (5):** Giselle Harumi Hayaka Silveira (Fiscal) · Wender Moreira
Silveira (Contador) · Taciane Moreira da Silveira (Contadora) · Dábila
Carneiro Costa Santana (Pessoal/RH) · Lucimara Gonçalves da Silva (Fiscal)

**Depoimentos (usar 3–4):** WSS Informática ("eficiência e precisão") ·
Cia das Piscinas — Rubens Roland (7 anos) · MF Móveis Forte (8 anos) ·
Agropecuária Fio de Ouro — Sérgio Borges Netto · Dress Code — Janaina Carla

**Diferenciais:** Contadores Nível Ouro, parceiros Conta Azul · Missão
(redução de custo, aumento de lucro, eficiência) · Atendimento ágil e
estratégico

**Contato:** Av. Fernando Correa da Costa, 1610, Sala 17, Galeria Xavier,
Jd. Kennedy, Cuiabá/MT, CEP 78065-000 · Seg–Sex 08:00–18:00 ·
Móvel: (65) 99292-0438 · Fixo: (65) 3359-6720

**CTAs:** "Abrir uma Empresa" · "Mudar de Contador" · WhatsApp

## Estrutura da página (ordem)

1. Header fixo com nav (Soluções, Segmentos, Equipe, Depoimentos, Contato)
2. Hero com proposta de valor + 2 CTAs
3. Faixa de credenciais (Nível Ouro / Conta Azul / anos de mercado)
4. Serviços (5 cards)
5. Segmentos de atuação (6 ícones)
6. Especialidades digitais (chips/lista)
7. Sobre + Missão/Visão/Valores
8. Equipe (5 cards com iniciais como avatar)
9. Depoimentos (3–4 cards)
10. FAQ curto (3 perguntas da seção "Dúvidas Frequentes" do site atual)
11. CTA final (Abrir Empresa / Mudar de Contador)
12. Footer com endereço, horário, telefones

## Requisitos técnicos (oráculo verificável)

- `index.html` único + `styles.css` + `script.js` (sem frameworks, sem build)
- Responsivo (mobile-first), menu hamburger em < 768px
- Seções com `id` para âncoras: `servicos`, `segmentos`, `equipe`,
  `depoimentos`, `contato`
- Meta description + title em pt-BR
- Sem imagens externas quebráveis: usar gradientes, ícones emoji/svg inline
- Acessibilidade básica: contraste, `alt`, labels, navegação por teclado
