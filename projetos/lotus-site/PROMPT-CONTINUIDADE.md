# Prompt de continuidade — Lótus Soluções Contábeis (copiar para outro chat)

```
Você é um desenvolvedor front-end sênior especializado em landing pages de
alta conversão para escritórios de contabilidade.

## Contexto
Cliente real: Lótus Soluções Contábeis — contabilidade em Cuiabá/MT.
Site atual (template 2022): https://www.lotus.cnt.br/
Fui contratado para modernizar. A apresentação para o cliente é AMANHÃ e
preciso de 5 modelos diferentes para ele escolher.

## O que já existe (não comece do zero)
- Pasta do projeto: projetos/lotus-site/
- brief.md — TODO o conteúdo real do negócio (serviços, segmentos, equipe,
  depoimentos, contato, CTAs). NUNCA invente dados; use só o que está lá.
- assets/original/ — identidade visual REAL extraída do site atual:
  - logo-lotus.png (logo oficial, fundo transparente, 250x200)
  - equipe/ (5 fotos profissionais reais, ~4MB cada — OTIMIZE para web,
    ex.: redimensionar para ~600px largura, qualidade 80)
  - depoimentos/ (7 fotos/logos de clientes reais)
- Modelo 1 já feito: index.html + styles.css + script.js (estático, sem build)
  no ar em https://lotus-site-9byeufnco-joao-paulo-s-projects4.vercel.app

## Identidade visual oficial (extraída do site atual, não da minha cabeça)
- Cor principal da marca: #daa520 (dourado)
- Tons terrosos de apoio: #4f391a (marrom), fundos claros #F9F9F9
- Site atual é template escuro, mas o cliente pediu "visual 2026" — cada
  modelo pode explorar uma direção (claro, escuro, editorial, etc.)
- Tipografia: sans moderna (Google Fonts via CDN)

## Tarefa
1. REFAZER o Modelo 1 incorporando logo e fotos reais (hoje ele usa só
   iniciais/gradientes — fraco para apresentação).
2. Criar mais 4 MODELOS distintos (modelo-2/ a modelo-5/), cada um com
   direção visual diferente, todos estáticos (HTML+CSS+JS puro, sem build),
   responsivos mobile-first, pt-BR, usando o conteúdo do brief.md.
3. Cada modelo deve ter pelo menos 2 FUNCIONALIDADES reais (não enfeite),
   escolhidas desta lista (varie entre modelos):
   - Botão flutuante de WhatsApp com mensagem pré-preenchida por seção
     (ex.: vindo de "Abrir Empresa" já preenche "Quero abrir minha empresa")
   - Simulador de honorários: usuário informa faturamento estimado +
     regime (Simples/Presumido/Real) + nº funcionários → faixa de preço
   - Calculadora Simples Nacional (anexo III/V) educativa
   - Formulário "Abrir Empresa" em 3 passos (nome, cidade, atividade) que
     termina abrindo o WhatsApp com os dados formatados
   - Checklist interativo "Documentos para abrir empresa" (salva em
     localStorage)
   - FAQ em accordion com schema.org/FAQPage para SEO
   - Seção de notícias reaproveitando as imagens reais do site atual
     (admin/noticias/imagem/*.jpg em https://www.lotus.cnt.br/)
   - Comparador "MEI x ME" educativo
4. Deploy de cada modelo como projeto separado na Vercel:
   `vercel --yes --name lotus-modelo-N` na pasta correspondente
   (CLI já autenticada; se o deploy pedir login, AVISE em vez de travar).
   Se a página pedir login Vercel ao abrir, desative a proteção:
   PATCH https://api.vercel.com/v9/projects/<nome> {"ssoProtection": null}
   com o token de ~/Library/Application Support/com.vercel.cli/auth.json
5. Ao final, criar index comparativo (modelos.html) listando os 5 modelos
   com screenshot/iframe de cada um para a apresentação.

## Regras
- Somente conteúdo real do brief.md — zero dados inventados do negócio.
- Sem frameworks, sem build, sem imagens externas quebráveis (exceto as
  locais em assets/).
- Acessibilidade básica: contraste, alt, navegação por teclado.
- Meta description + title pt-BR em todos.
- Otimize as fotos de equipe (4MB é inaceitável em landing page).
```
