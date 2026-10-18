'use client';
import { useEffect, useState, useRef } from 'react';
import { Brain } from 'lucide-react';
import { Transformer } from 'markmap-lib';
import { Markmap } from 'markmap-view';
import { INode } from 'markmap-common';
import { API_URL } from '../services/api';

const transformer = new Transformer();

type MindmapData = {
  messages: Array<{
    content: string;
    isBot: boolean;
  }>;
  timestamp: string;
  markdown?: string; // mindmap déjà générée pour ce message (cache)
};

const MAX_BRANCHES = 6;
const MAX_LEAVES = 4;
const MAX_WORDS = 6;

// Raccourcit un libellé : on garde le nom avant « : » ou « – », puis quelques mots
const shortLabel = (text: string): string => {
  let label = text
    .replace(/\*\*|\*|`|_/g, '')
    .replace(/^#+\s*/, '')
    .replace(/^(\d+[.)]|\d️⃣|[-•▶])\s*/, '')
    .trim();
  // Ce qui suit « : », « – », « → » ou « ( » est une explication : on ne garde que le début
  const head = label.split(/\s[–—-]\s|\s?:\s|\s?→|\s\(/)[0].trim();
  if (head) label = head;
  label = label.replace(/[.;:,]+$/, '').trim();
  const words = label.split(/\s+/);
  return words.length > MAX_WORDS ? words.slice(0, MAX_WORDS).join(' ') + '…' : label;
};

// Version locale condensée, utilisée si la génération par l'IA échoue :
// titres de sections comme branches, et seulement le nom des éléments de liste
const buildFallbackMarkdown = (content: string): string => {
  const branches: { label: string; leaves: string[] }[] = [];

  for (const line of content.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('|')) continue;

    const isHeading = /^#{1,6}\s/.test(trimmed) || /^\*\*[^*]+\*\*$/.test(trimmed);
    const isTopLevelItem = /^(\d+[.)]|\d️⃣|[-•▶])\s/.test(trimmed) && !/^\s{2,}/.test(line);

    if (isHeading) {
      const label = shortLabel(trimmed);
      if (label && !/^m[ée]thodes/i.test(label)) branches.push({ label, leaves: [] });
    } else if (isTopLevelItem && branches.length > 0) {
      const leaf = shortLabel(trimmed);
      const branch = branches[branches.length - 1];
      if (leaf && branch.leaves.length < MAX_LEAVES) branch.leaves.push(leaf);
    }
  }

  // On ne garde que les branches qui ont du contenu
  const kept = branches.filter(b => b.leaves.length > 0).slice(0, MAX_BRANCHES);
  let markdown = '# 🌟 Idées clés\n';
  for (const branch of kept) {
    markdown += `## ${branch.label}\n`;
    for (const leaf of branch.leaves) markdown += `### ${leaf}\n`;
  }
  return markdown;
};

const MindmapPage = () => {
  const [mindmapData, setMindmapData] = useState<MindmapData | null>(null);
  const [markdown, setMarkdown] = useState<string>('');
  const [status, setStatus] = useState<'loading' | 'ready' | 'fallback'>('loading');
  const svgRef = useRef<SVGSVGElement>(null);
  const markmapRef = useRef<Markmap | null>(null);
  const [exportFormat, setExportFormat] = useState<'markdown' | 'html' | 'png'>('markdown');

  useEffect(() => {
    const data = localStorage.getItem('mindmapData');
    if (data) {
      const parsedData = JSON.parse(data) as MindmapData;
      setMindmapData(parsedData);
    }
  }, []);

  // Génère une mindmap concise (seulement les idées clés) via le backend
  useEffect(() => {
    if (!mindmapData) return;
    const lastBotMessage = mindmapData.messages[mindmapData.messages.length - 1];
    if (!lastBotMessage) return;

    if (mindmapData.markdown) {
      setMarkdown(mindmapData.markdown);
      setStatus('ready');
      return;
    }

    let cancelled = false;
    const generate = async () => {
      setStatus('loading');
      try {
        const token = localStorage.getItem('authToken');
        const res = await fetch(`${API_URL}/api/mindmap/generate`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify({ content: lastBotMessage.content })
        });
        const data = await res.json();
        if (!res.ok || !data.success || !data.markdown) {
          throw new Error(data.error || 'Génération impossible');
        }
        if (cancelled) return;
        setMarkdown(data.markdown);
        setStatus('ready');
        // Mise en cache pour ne pas régénérer à chaque rechargement
        localStorage.setItem('mindmapData', JSON.stringify({ ...mindmapData, markdown: data.markdown }));
      } catch (error) {
        console.error('Erreur de génération de la mindmap:', error);
        if (cancelled) return;
        setMarkdown(buildFallbackMarkdown(lastBotMessage.content));
        setStatus('fallback');
      }
    };
    generate();
    return () => { cancelled = true; };
  }, [mindmapData]);

  useEffect(() => {
    if (!markdown || !svgRef.current) return;

    // Cleanup any existing mindmap
    if (markmapRef.current) {
      markmapRef.current.destroy();
      markmapRef.current = null;
    }
    svgRef.current.innerHTML = '';

    try {
      const { root } = transformer.transform(markdown);
      markmapRef.current = Markmap.create(svgRef.current, {
        autoFit: true,
        maxWidth: 260,        // les libellés longs passent à la ligne
        spacingVertical: 12,
        spacingHorizontal: 100,
        paddingX: 12,
        color: (node: INode) => {
          const colors = ['#7c3aed', '#4f46e5', '#06b6d4', '#0ea5e9', '#6366f1'];
          const level = (node.payload as any)?.depth || 0;
          return colors[level % colors.length];
        },
      }, root);
    } catch (error) {
      console.error('Error creating mindmap:', error);
    }

    return () => {
      if (markmapRef.current) {
        markmapRef.current.destroy();
        markmapRef.current = null;
      }
    };
  }, [markdown]);

  // Fonction pour exporter la mindmap
  const handleExport = async () => {
    try {
      if (!mindmapData?.messages.length) {
        alert('Aucune donnée à exporter');
        return;
      }

      if (exportFormat === 'png') {
        // Pour le format PNG, on exporte directement le SVG visible
        if (!svgRef.current) {
          throw new Error('Le SVG n\'est pas disponible');
        }

        // Créer une copie du SVG pour la manipulation
        const svgClone = svgRef.current.cloneNode(true) as SVGSVGElement;
        const svgData = new XMLSerializer().serializeToString(svgClone);

        // Créer un canvas pour convertir le SVG en PNG
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const img = new Image();

        // Définir la taille du canvas
        canvas.width = svgRef.current.clientWidth;
        canvas.height = svgRef.current.clientHeight;

        // Convertir le SVG en PNG
        const svgBlob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
        const url = URL.createObjectURL(svgBlob);

        img.onload = () => {
          if (ctx) {
            ctx.fillStyle = 'white';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.drawImage(img, 0, 0);

            canvas.toBlob((blob) => {
              if (blob) {
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'mindmap.png';
                document.body.appendChild(a);
                a.click();
                URL.revokeObjectURL(url);
                document.body.removeChild(a);
              }
            }, 'image/png');
          }
        };
        img.src = url;
        return;
      }

      // Pour les autres formats, on envoie au backend
      const response = await fetch(`${API_URL}/api/mindmap/export`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('authToken')}`
        },
        body: JSON.stringify({
          format: exportFormat,
          content: markdown
        })
      });

      const data = await response.json();
      if (!data.success) {
        throw new Error(data.error || 'Erreur lors de l\'export');
      }

      // Pour les formats markdown et HTML
      const content = exportFormat === 'html' ? data.content : markdown;
      const type = exportFormat === 'html' ? 'text/html' : 'text/markdown';
      const extension = exportFormat === 'html' ? 'html' : 'md';

      const blob = new Blob([content], { type });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `mindmap.${extension}`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      console.error('Erreur lors de l\'export:', error);
      alert('Erreur lors de l\'export: ' + (error instanceof Error ? error.message : String(error)));
    }
  };

  if (!mindmapData) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gradient-to-br from-purple-50 via-blue-50 to-indigo-50">
        <div className="p-8 text-center shadow-xl rounded-2xl bg-white/80 backdrop-blur-sm">
          <div className="flex items-center justify-center w-16 h-16 mx-auto mb-4 rounded-full bg-gradient-to-r from-purple-600 to-blue-600">
            <Brain className="w-8 h-8 text-white" />
          </div>
          <h1 className="mb-2 text-xl font-bold text-gray-800">Aucune mindmap disponible</h1>
          <p className="text-gray-600">Veuillez d&apos;abord générer des idées dans le chatbot.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-8 bg-gradient-to-br from-purple-50 via-blue-50 to-indigo-50">
      <div className="mx-auto max-w-7xl">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-transparent bg-gradient-to-r from-purple-600 to-blue-600 bg-clip-text">
              Mindmap des Idées
            </h1>
            <p className="text-gray-600">Visualisation de vos idées générées</p>
          </div>
          <div className="flex flex-row gap-2">
            <button
              onClick={() => window.location.href = '/Chatbot'}
              className="px-4 py-2 text-purple-600 transition-all duration-200 border border-purple-200 rounded-xl hover:bg-purple-50"
            >
              Retour au chat
            </button>
          </div>
        </div>

        {status === 'fallback' && (
          <div className="px-4 py-2 mb-4 text-sm border text-amber-700 border-amber-200 rounded-xl bg-amber-50">
            La synthèse par l&apos;IA n&apos;a pas pu être générée : affichage d&apos;une version simplifiée de la réponse.
          </div>
        )}

        <div className="relative p-8 overflow-hidden shadow-xl bg-white/80 backdrop-blur-sm rounded-2xl" style={{ height: 'calc(100vh - 200px)' }}>
          {status === 'loading' && (
            <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-white/70">
              <div className="flex items-center justify-center mb-3 rounded-full w-14 h-14 bg-gradient-to-r from-purple-600 to-blue-600 animate-pulse">
                <Brain className="text-white w-7 h-7" />
              </div>
              <p className="text-sm text-purple-700">Extraction des idées clés…</p>
            </div>
          )}
          <svg ref={svgRef} style={{ width: '100%', height: '100%' }} />
        </div>
      </div>

      {/* Boutons d'export et d'envoi par email */}
      <div className="fixed flex space-x-4 bottom-4 right-4">
        <button
          onClick={async () => {
            if (!svgRef.current) {
              alert('Mindmap non générée');
              return;
            }
            const svg = svgRef.current;
            // Récupérer la taille réelle du SVG (fallback si non défini)
            let width = 1200;
            let height = 800;
            if (svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width && svg.viewBox.baseVal.height) {
              width = svg.viewBox.baseVal.width;
              height = svg.viewBox.baseVal.height;
            } else if (svg.width?.baseVal?.value && svg.height?.baseVal?.value) {
              width = svg.width.baseVal.value;
              height = svg.height.baseVal.value;
            } else if (svg.clientWidth && svg.clientHeight) {
              width = svg.clientWidth;
              height = svg.clientHeight;
            }
            // Convertir le SVG en base64 de façon robuste
            const serializer = new XMLSerializer();
            let svgString = serializer.serializeToString(svg);
            // Corriger les caractères spéciaux pour base64
            svgString = svgString.replace(/\uFEFF/g, '');
            const svgBase64 = 'data:image/svg+xml;base64,' + window.btoa(unescape(encodeURIComponent(svgString)));
            const img = new window.Image();
            img.crossOrigin = 'anonymous';
            img.onload = () => {
              const canvas = document.createElement('canvas');
              canvas.width = width;
              canvas.height = height;
              const ctx = canvas.getContext('2d');
              if (ctx) {
                ctx.fillStyle = 'white';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, 0, 0, width, height);
                canvas.toBlob((blob) => {
                  if (blob) {
                    const downloadUrl = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = downloadUrl;
                    a.download = 'mindmap.png';
                    document.body.appendChild(a);
                    a.click();
                    URL.revokeObjectURL(downloadUrl);
                    document.body.removeChild(a);
                  } else {
                    alert('Erreur lors de la génération du PNG.');
                  }
                }, 'image/png');
              } else {
                alert('Impossible de créer le contexte du canvas.');
              }
            };
            img.onerror = () => {
              alert('Erreur lors du rendu de l\'image.');
            };
            img.src = svgBase64;
          }}
          className="px-4 py-2 font-bold text-white bg-blue-500 rounded hover:bg-blue-700"
        >
          Télécharger l'image
        </button>
      </div>
    </div>
  );
};

export default MindmapPage;
